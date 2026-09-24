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
    if (state.guestAccountMode === 'existing_template' && !state.guestCredentialId) {
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
    return errors;
  }

  function preview(state, credentials) {
    const credential = credentials.find(row => String(row.id) === String(state.guestCredentialId));
    const username = credential?.username || state.sshUsername || 'clouduser';
    const existingAccount = state.guestAccountMode === 'existing_template';
    const text = [
      '#cloud-config',
      '# Podgląd bez sekretów. Pełną konfigurację przygotuje worker.',
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
    const credential = selectField(
      state.guestAccountMode === 'existing_template'
        ? 'Dane dostępowe do istniejącego konta'
        : 'Użytkownik, hasło lub klucz z Dostępów',
      'cloud_init_guest_credential_id',
      state.guestAccountMode === 'existing_template'
        ? [{ value: '', label: 'Wybierz konto istniejące w template' }, ...choices]
        : [{ value: '', label: 'Użytkownik i klucz publiczny z parametrów VM' }, ...choices],
      state.guestCredentialId,
      {
        wide: true,
        help: state.guestAccountMode === 'existing_template'
          ? 'Username credentiala musi odpowiadać kontu już obecnemu w template. Cloud-init go nie modyfikuje; credential zostaje przypisany do wykonania kolejnych etapów.'
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
        node('pre', { class: 'code-block', text: preview(state, data.credentials || []) })));
    return panel;
  }

  parts.cloudInit = { enabled, toggleStep, validate, preview, render };
  registerExtension('blueprint-wizard-cloud-init', () => {});
})();
