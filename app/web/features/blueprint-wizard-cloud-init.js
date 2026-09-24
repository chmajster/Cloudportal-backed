'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function enabled(state) {
    return state.providerType === 'proxmox' && (state.advancedWorkflow
      ? state.workflow.some(step => step.type === 'cloud_init')
      : state.cloudInitEnabled !== false);
  }

  function toggleStep(state, checked) {
    state.cloudInitEnabled = checked;
    if (!state.advancedWorkflow) return;
    const existing = state.workflow.filter(step => step.type === 'cloud_init');
    if (!checked) {
      toggleAwxStep(state, false);
      const removed = new Set(existing.map(step => step.id));
      state.workflow = state.workflow.filter(step => !removed.has(step.id)).map(step => ({
        ...step, depends_on: (step.depends_on || []).filter(id => !removed.has(id)),
      }));
      return;
    }
    if (existing.length) return;
    const ids = new Set(state.workflow.map(step => step.id));
    let id = 'cloud_init';
    for (let index = 2; ids.has(id); index += 1) id = 'cloud_init_' + index;
    state.workflow.unshift({ id, type: 'cloud_init', depends_on: [], conditions: {}, retry: 0, timeout: 600, rollback: null });
    state.workflow = state.workflow.map(step => ['terraform_plan', 'terraform_apply'].includes(step.type)
      ? { ...step, depends_on: [...new Set([...(step.depends_on || []), id])] }
      : step);
  }

  function validate(state) {
    const errors = {};
    if (!enabled(state)) return errors;
    const awxStep = state.advancedWorkflow
      ? state.workflow.some(step => step.type === 'register_awx')
      : Boolean(state.awxEnabled);
    if (awxStep && !state.awxCredentialId) {
      errors.awx_credential_id = 'Wybierz połączenie AWX dla automatycznego onboardingu.';
    }
    if (state.guestAccountMode === 'existing_template'
        && !state.guestCredentialId
        && !state.templateGuestCredentialId) {
      errors.guest_credential_id = 'Wybierz dane dostępowe SSH dla konta istniejącego w template.';
    }
    if (!state.advancedWorkflow) return errors;
    const seeds = state.workflow.filter(step => step.type === 'cloud_init');
    if (seeds.length !== 1 || Object.keys(seeds[0].conditions || {}).length || seeds[0].retry || seeds[0].rollback) {
      errors.workflow = 'Cloud-init wymaga jednego bezwarunkowego kroku konfiguracji bez rollbacku.';
      return errors;
    }
    const byId = new Map(state.workflow.map(step => [step.id, step]));
    const precedes = (id, visited = new Set()) => {
      if (id === seeds[0].id) return true;
      if (visited.has(id)) return false;
      visited.add(id);
      return (byId.get(id)?.depends_on || []).some(parent => precedes(parent, new Set(visited)));
    };
    if (state.workflow.some(step => ['terraform_plan', 'terraform_apply'].includes(step.type) && !precedes(step.id))) {
      errors.workflow = 'Cloud-init musi być zależnością każdego kroku Terraform Plan i Terraform Apply.';
    }
    if (state.awxEnabled && !state.workflow.some(step => step.type === 'register_awx')) {
      errors.workflow = 'Włączony onboarding AWX wymaga kroku register_awx w workflow.';
    }
    return errors;
  }

  function toggleAwxStep(state, checked) {
    state.awxEnabled = checked;
    if (!state.advancedWorkflow) return;
    const awxSteps = state.workflow.filter(step => step.type === 'register_awx');
    if (!checked) {
      const removed = new Set(awxSteps.map(step => step.id));
      state.workflow = state.workflow.filter(step => !removed.has(step.id)).map(step => ({
        ...step, depends_on: (step.depends_on || []).filter(id => !removed.has(id)),
      }));
      return;
    }
    if (awxSteps.length) return;

    let ipStep = state.workflow.find(step => step.type === 'wait_for_ip');
    if (!ipStep) {
      const apply = state.workflow.find(step => step.type === 'terraform_apply');
      const ids = new Set(state.workflow.map(step => step.id));
      let ipId = 'guest_ip';
      for (let index = 2; ids.has(ipId); index += 1) ipId = 'guest_ip_' + index;
      ipStep = {
        id: ipId,
        type: 'wait_for_ip',
        depends_on: apply ? [apply.id] : [],
        conditions: {},
        retry: 0,
        timeout: 600,
        rollback: null,
      };
      state.workflow.push(ipStep);
    }
    const ids = new Set(state.workflow.map(step => step.id));
    let awxId = 'awx';
    for (let index = 2; ids.has(awxId); index += 1) awxId = 'awx_' + index;
    state.workflow.push({
      id: awxId,
      type: 'register_awx',
      depends_on: [ipStep.id],
      conditions: {},
      retry: 1,
      timeout: 600,
      rollback: null,
    });
  }

  async function discoverAwx(state, credentialId) {
    state.awxDiscovery = null;
    state.awxDiscoveryError = '';
    if (!credentialId) return;
    try {
      state.awxDiscovery = await api('/credentials/' + Number(credentialId) + '/awx/discovery', {
        headers: parts.core.scopeHeaders(state),
      });
    } catch (error) {
      state.awxDiscoveryError = error.message;
    }
  }

  function awxPanel(state, data, rerender, capture) {
    const awxCredentials = (data.credentials || []).filter(row => row.type === 'awx');
    const active = Boolean(state.awxEnabled);
    const toggle = checkboxField(
      'Po utworzeniu VM dodaj ją automatycznie do AWX',
      'awx_enabled',
      active
    );
    toggle.querySelector('input').addEventListener('change', event => {
      capture();
      toggleAwxStep(state, event.currentTarget.checked);
      rerender();
    });

    const panel = node('section', { class: 'blueprint-wizard-inline-panel wide', 'data-awx-onboarding': 'true' },
      node('div', { class: 'blueprint-wizard-section-heading wide' },
        node('strong', { text: 'AWX — dynamiczny onboarding po Cloud-init' }),
        node('span', { class: 'muted', text: 'Sekret AWX pozostaje w CloudPortal. Do cloud-init nie trafia login, hasło ani token AWX.' })),
      toggle
    );
    if (!active) return panel;

    const credential = selectField('Połączenie AWX', 'awx_credential_id', [
      { value: '', label: awxCredentials.length ? 'Wybierz AWX' : 'Brak zapisanych połączeń AWX' },
      ...awxCredentials.map(row => ({ value: row.id, label: row.name + ' · ' + row.endpoint })),
    ], state.awxCredentialId, { required: true, wide: true });
    credential.querySelector('select').addEventListener('change', async event => {
      capture();
      state.awxCredentialId = event.currentTarget.value;
      state.awxInventoryId = '';
      state.awxJobTemplateId = '';
      await discoverAwx(state, state.awxCredentialId);
      rerender();
    });

    const discovery = state.awxDiscovery || {};
    const inventory = selectField('Inventory', 'awx_inventory_id', [
      { value: '', label: 'Automatycznie — użyj/utwórz „' + (state.awxInventoryName || 'CloudPortal') + '”' },
      ...(discovery.inventories || []).map(row => ({ value: row.id, label: row.name })),
    ], state.awxInventoryId, { wide: true });
    inventory.querySelector('select').addEventListener('change', event => {
      state.awxInventoryId = event.currentTarget.value;
    });

    const inventoryName = field('Nazwa inventory tworzonego automatycznie', 'awx_inventory_name', {
      value: state.awxInventoryName || 'CloudPortal',
      wide: true,
      help: 'Jeśli nie wskażesz istniejącego inventory, CloudPortal odszuka tę nazwę, a następnie spróbuje ją utworzyć.',
    });
    inventoryName.querySelector('input').addEventListener('input', event => {
      state.awxInventoryName = event.currentTarget.value;
    });

    const groupEnv = checkboxField('Twórz/przypisuj grupę env-<environment>', 'awx_group_environment', state.awxGroupByEnvironment);
    groupEnv.querySelector('input').addEventListener('change', event => {
      state.awxGroupByEnvironment = event.currentTarget.checked;
    });
    const groupApmid = checkboxField('Twórz/przypisuj grupę apmid-<APMID>', 'awx_group_apmid', state.awxGroupByApmid);
    groupApmid.querySelector('input').addEventListener('change', event => {
      state.awxGroupByApmid = event.currentTarget.checked;
    });

    const jobTemplate = selectField('Job Template po onboardingu (opcjonalnie)', 'awx_job_template_id', [
      { value: '', label: 'Nie uruchamiaj automatycznie' },
      ...(discovery.job_templates || []).map(row => ({ value: row.id, label: row.name })),
    ], state.awxJobTemplateId, { wide: true });
    jobTemplate.querySelector('select').addEventListener('change', event => {
      state.awxJobTemplateId = event.currentTarget.value;
    });

    const removeOnDestroy = checkboxField(
      'Usuń host z AWX po usunięciu VM',
      'awx_remove_on_destroy',
      state.awxRemoveOnDestroy !== false
    );
    removeOnDestroy.querySelector('input').addEventListener('change', event => {
      state.awxRemoveOnDestroy = event.currentTarget.checked;
    });

    panel.append(
      credential,
      state.awxDiscoveryError
        ? node('div', { class: 'blueprint-wizard-info danger' },
            node('strong', { text: 'Autodiscovery AWX nie powiodło się' }),
            node('span', { text: state.awxDiscoveryError }))
        : (state.awxDiscovery
            ? node('div', { class: 'blueprint-wizard-info' },
                node('strong', { text: 'AWX połączony' }),
                node('span', { text: 'API: ' + (discovery.api_base || '—')
                  + ' · inventory: ' + (discovery.inventories || []).length
                  + ' · job templates: ' + (discovery.job_templates || []).length }))
            : null),
      inventory,
      inventoryName,
      groupEnv,
      groupApmid,
      jobTemplate,
      removeOnDestroy,
      node('p', { class: 'muted', text: 'Po uzyskaniu adresu IP CloudPortal utworzy lub zaktualizuje host w AWX. Operacja jest idempotentna — ponowienie workflow aktualizuje ten sam host zamiast tworzyć duplikat. Usuwanie hosta z AWX po terraform destroy jest best-effort i nie blokuje usunięcia VM, gdy AWX jest chwilowo niedostępny.' })
    );
    return panel;
  }

  function preview(state, credentials) {
    const credential = credentials.find(row => String(row.id) === String(state.guestCredentialId));
    const templateCredential = credentials.find(row =>
      String(row.id) === String(state.templateGuestCredentialId));
    const username = (state.guestAccountMode === 'existing_template' ? templateCredential?.username : null)
      || credential?.username || state.sshUsername || 'clouduser';
    const existingAccount = state.guestAccountMode === 'existing_template';
    const text = [
      '#cloud-config',
      '# Podgląd bez sekretów. Pełną konfigurację przygotuje worker.',
      ...(templateCredential && !existingAccount ? [
        '# Istniejące konto z template do późniejszego dostępu SSH: ' + templateCredential.username,
        '# Cloud-init nie modyfikuje tego konta, chyba że wybrano je także jako konto zarządzane.',
      ] : []),
      ...(existingAccount ? [
        '# Konto ' + JSON.stringify(username) + ' już istnieje w template.',
        '# Cloud-init nie utworzy użytkownika, nie zmieni jego hasła, kluczy SSH ani sudo.',
        '# Wybrany credential będzie używany w późniejszych etapach dostępu do VM.',
      ] : [
        'users:',
        '  - name: ' + JSON.stringify(username),
        '    shell: /bin/sh',
        ...(credential ? [
          '    # Hasło z Dostępów zostanie zapisane wyłącznie jako solony hash.',
          '    # Z klucza prywatnego zostanie wyprowadzony tylko klucz publiczny.',
        ] : (state.sshPublicKey ? ['    ssh_authorized_keys:', '      - ' + JSON.stringify(state.sshPublicKey)] : [])),
        'chpasswd:',
        '  expire: false',
      ]),
    ];
    if (state.installQemuGuestAgent) text.push(
      'package_update: true', 'packages:', '  - qemu-guest-agent', 'runcmd:',
      '  # Worker uwzględnia jednostki static/indirect oraz OpenRC.',
      '  - [systemctl, enable, qemu-guest-agent]',
      '  - [systemctl, start, qemu-guest-agent]',
      '  - [systemctl, is-active, --quiet, qemu-guest-agent]',
    );
    if (state.awxEnabled) text.push(
      '',
      '# AWX onboarding wykonuje backend po uzyskaniu IP.',
      '# Żaden login, hasło ani token AWX nie jest umieszczany w tym user-data.',
    );
    text.push('', '# Oddzielny network-config na tym samym nośniku CIDATA:',
      '# ' + (state.ipMode === 'dhcp' ? 'DHCP na głównym interfejsie (dopasowanie po MAC).'
        : state.ipMode === 'ipam' ? 'Adres, brama i DNS z przydziału IPAM.'
          : 'Statyczny adres: ' + state.ipv4Address + ', brama: ' + state.ipv4Gateway));
    return text.join('\n');
  }

  function render({ state, data, rerender, capture }) {
    if (state.providerType !== 'proxmox') return null;
    const active = enabled(state);
    const toggle = checkboxField('Utwórz Cloud-init przy pierwszym starcie VM', 'cloud_init_enabled', active);
    toggle.querySelector('input').addEventListener('change', event => {
      const checked = event.currentTarget.checked;
      capture();
      toggleStep(state, checked);
      rerender();
    });
    const panel = node('section', { class: 'blueprint-wizard-inline-panel', 'data-cloud-init-config': 'true' },
      node('h4', { text: 'Cloud-init — użytkownik i przygotowanie systemu' }), toggle);
    if (!active) {
      panel.append(node('p', { class: 'muted', text: 'Pozostaje starszy tryb konfiguracji. DHCP bez działającego agenta w template nie umożliwia bootstrapu przez SSH.' }));
      return panel;
    }
    const accountMode = selectField('Konto systemowe w VM', 'cloud_init_guest_account_mode', [
      { value: 'cloud_init_managed', label: 'Utwórz / skonfiguruj konto przez Cloud-init' },
      { value: 'existing_template', label: 'Użyj istniejącego konta z template' },
    ], state.guestAccountMode || 'cloud_init_managed', {
      wide: true,
      help: 'Tryb istniejącego konta zachowuje użytkownika z obrazu/template bez zmiany hasła, kluczy SSH i sudo. Credential służy później do logowania i automatyzacji.',
    });
    accountMode.querySelector('select').addEventListener('change', event => {
      const value = event.currentTarget.value;
      capture();
      state.guestAccountMode = value;
      rerender();
    });
    const choices = window.BlueprintProvisioningGuards.guestCredentialChoices(data.credentials || []);
    const templateCredential = (data.credentials || []).find(row =>
      String(row.id) === String(state.templateGuestCredentialId));
    if (templateCredential) {
      panel.append(node('div', { class: 'blueprint-wizard-info' },
        node('strong', { text: 'Konto istniejące w template: ' + (templateCredential.username || templateCredential.name) }),
        node('span', { text: 'Ten Credential może być używany przez uwierzytelnione kroki SSH i dalszą automatyzację. Cloud-init zachowa to konto, dopóki nie wybierzesz go jawnie jako konta zarządzanego.' })));
    }
    const credential = selectField(
      state.guestAccountMode === 'existing_template'
        ? 'Dane dostępowe do istniejącego konta'
        : 'Użytkownik, hasło lub klucz z Dostępów',
      'cloud_init_guest_credential_id',
      state.guestAccountMode === 'existing_template'
        ? [{ value: '', label: state.templateGuestCredentialId
            ? 'Użyj Credentiala istniejącego konta wybranego wcześniej'
            : 'Wybierz konto istniejące w template' }, ...choices]
        : [{ value: '', label: 'Użytkownik i klucz publiczny z parametrów VM' }, ...choices],
      state.guestCredentialId,
      {
        wide: true,
        help: state.guestAccountMode === 'existing_template'
          ? 'Username credentiala musi odpowiadać kontu już obecnemu w template. Możesz użyć Credentiala wybranego wcześniej jako istniejące konto lokalne albo wskazać go tutaj. Cloud-init go nie modyfikuje.'
          : 'Zapisujemy wyłącznie ID dostępu. Hasło ani klucz prywatny nie są pobierane do przeglądarki.',
      },
    );
    credential.querySelector('select').addEventListener('change', event => {
      const value = event.currentTarget.value;
      capture();
      state.guestCredentialId = value;
      rerender();
    });
    panel.append(accountMode, credential,
      node('p', { class: 'muted', text: state.guestAccountMode === 'existing_template'
        ? 'Cloud-init wykona konfigurację systemu, sieci i opcjonalnie QEMU Guest Agent, ale zachowa istniejące konto z template. Wybrany credential będzie używany później przez etapy wymagające dostępu do systemu gościa.'
        : 'Konfiguracja trafi na nośnik NoCloud ISO (CIDATA) przez API Proxmoxa przed uruchomieniem VM. Wymagany jest Linux z cloud-init i aktywny storage obsługujący ISO, np. local. SSH do noda ani znany adres VM nie są potrzebne.' }),
      node('p', { class: 'muted', text: 'Opcja „Instaluj QEMU Guest Agent automatycznie” poniżej dodaje pakiet oraz włączenie i uruchomienie usługi do Cloud-init. Nie instaluje agenta przez SSH.' }),
      node('details', { class: 'advanced-options' },
        node('summary', { text: 'Podgląd Cloud-init bez sekretów' }),
        node('pre', { class: 'code-block', text: preview(state, data.credentials || []) })),
      awxPanel(state, data, rerender, capture));
    return panel;
  }

  parts.cloudInit = { enabled, toggleStep, toggleAwxStep, validate, preview, render, discoverAwx };
  registerExtension('blueprint-wizard-cloud-init', () => {});
})();
