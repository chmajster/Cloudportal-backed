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
    if (!checked) {
      if (parts.awx?.toggleStep) parts.awx.toggleStep(state, false);
      else state.awxEnabled = false;
      state.guestAccountMode = 'cloud_init_managed';
    }
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
    if (state.guestAccountMode === 'existing_template' && !enabled(state)) {
      errors.cloud_init_enabled = 'Tryb istniejącego konta z template wymaga kroku Cloud-init.';
      return errors;
    }
    if (!enabled(state)) return errors;
    if (state.guestAccountMode === 'existing_template'
        && !state.guestCredentialId
        && !state.templateGuestCredentialId) {
      errors.guest_credential_id = 'Wybierz dane dostępowe SSH dla konta istniejącego w template.';
    }
    if (state.guestAccountMode === 'cloud_init_managed'
        && state.sshPassword
        && state.sshPassword.length < 12) {
      errors.cloud_init_ssh_password = 'Hasło SSH musi mieć co najmniej 12 znaków.';
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
        '# Wybrany Dostęp będzie używany w późniejszych etapach dostępu do VM.',
      ] : [
        'users:',
        '  - name: ' + JSON.stringify(username),
        '    shell: /bin/sh',
        ...(credential ? [
          ...(credential.supports_cloud_init_password === true
            ? ['    # Hasło z Dostępu zostanie zapisane wyłącznie jako solony hash.']
            : []),
          ...(credential.supports_cloud_init_ssh_key === true
            ? ['    # Z klucza prywatnego Dostępu zostanie wyprowadzony tylko klucz publiczny.']
            : (state.sshPublicKey
                ? ['    ssh_authorized_keys:', '      - ' + JSON.stringify(state.sshPublicKey)]
                : [])),
        ] : [
          ...(state.sshPassword || state.managedGuestCredentialId
            ? ['    # Hasło z ustawień ręcznych zostanie użyte wyłącznie jako solony hash.']
            : []),
          ...(state.sshPublicKey
            ? ['    ssh_authorized_keys:', '      - ' + JSON.stringify(state.sshPublicKey)]
            : []),
        ]),
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

    const panel = node('section', { class: 'blueprint-wizard-inline-panel blueprint-wizard-cloud-init-access', 'data-cloud-init-config': 'true' },
      node('div', { class: 'blueprint-wizard-section-heading wide' },
        node('strong', { text: 'Cloud-init i dostęp SSH' }),
        node('span', { class: 'muted', text: 'Konto systemowe i dostęp do VM są konfigurowane tutaj, a nie w parametrach sprzętowych VM.' })),
      toggle);

    if (!active) {
      panel.append(node('div', { class: 'blueprint-wizard-info wide' },
        node('strong', { text: 'Cloud-init wyłączony' }),
        node('span', { text: 'Pola użytkownika SSH, hasła i klucza publicznego są ukryte, ponieważ bez kroku Cloud-init nie zostałyby zastosowane do systemu gościa.' })));
      return panel;
    }

    const accountMode = selectField('Tryb konta systemowego', 'cloud_init_guest_account_mode', [
      { value: 'cloud_init_managed', label: 'Utwórz / skonfiguruj konto przez Cloud-init' },
      { value: 'existing_template', label: 'Użyj istniejącego konta z template' },
    ], state.guestAccountMode || 'cloud_init_managed', {
      wide: true,
      help: 'Dla konta istniejącego w template Cloud-init nie zmienia hasła, kluczy SSH ani sudo. Wybrany Dostęp służy wtedy do późniejszego logowania i automatyzacji.',
    });
    accountMode.querySelector('select').addEventListener('change', event => {
      capture();
      state.guestAccountMode = event.currentTarget.value;
      if (state.guestAccountMode === 'existing_template') {
        state.guestCredentialId = '';
      } else {
        state.templateGuestCredentialId = '';
      }
      if (state.guestAccountMode === 'existing_template') {
        state.managedGuestCredentialId = '';
        state.guestCredentialManaged = false;
        state.sshPassword = '';
      }
      rerender();
    });

    const choices = window.BlueprintProvisioningGuards.guestCredentialChoices(data.credentials || []);
    const eligibleCredentialIds = new Set(choices.map(choice => String(choice.value)));
    const sshAccesses = (data.credentials || []).filter(row =>
      row.type === 'ssh' && eligibleCredentialIds.has(String(row.id)));
    const accessCredentialId = state.guestAccountMode === 'existing_template'
      ? (state.templateGuestCredentialId || state.guestCredentialId || '')
      : (state.guestCredentialId || '');
    const credentialField = parts.ui.credentialPicker(
      state.guestAccountMode === 'existing_template'
        ? 'Dostęp do konta istniejącego w template'
        : 'Dostęp zarządzany przez Cloud-init',
      'cloud_init_access_credential_id',
      sshAccesses,
      accessCredentialId,
      value => {
        capture();
        if (state.guestAccountMode === 'existing_template') {
          state.templateGuestCredentialId = value;
          state.guestCredentialId = '';
        } else {
          state.templateGuestCredentialId = '';
          if (value) {
            state.guestCredentialId = value;
            state.managedGuestCredentialId = '';
            state.guestCredentialManaged = false;
            state.sshPassword = '';
          } else {
            state.guestCredentialId = '';
            state.guestCredentialManaged = Boolean(
              state.managedGuestCredentialId || state.sshPassword
            );
          }
        }
        rerender();
      },
      {
        noneLabel: state.guestAccountMode === 'existing_template'
          ? 'Nie wybrano Dostępu do konta z template'
          : 'Użyj ustawień ręcznych',
        noneDescription: state.guestAccountMode === 'existing_template'
          ? 'Wybierz zapisany Dostęp SSH do konta już obecnego w template.'
          : 'Cloud-init użyje użytkownika, hasła i/lub klucza publicznego z ustawień ręcznych.',
        description: state.guestAccountMode === 'existing_template'
          ? 'Sekret pozostaje w backendzie. Cloud-init zachowa konto z template bez zmiany jego hasła, kluczy i sudo.'
          : 'Dostęp używany przez Cloud-init jest zapisywany w Blueprintcie wyłącznie jako ID; sekret nie trafia do przeglądarki.',
        emptyTitle: 'Brak Dostępów SSH',
        emptyText: 'Dodaj Dostęp typu SSH / Linux albo użyj konfiguracji ręcznej.',
      }
    );

    const selectedCredential = (data.credentials || []).find(row =>
      String(row.id) === String(accessCredentialId));
    const credentialSummary = selectedCredential
      ? node('div', { class: 'blueprint-wizard-info wide' },
          node('strong', { text: 'Wybrany Dostęp SSH' }),
          node('span', { text: [
            'Użytkownik: ' + (selectedCredential.username || selectedCredential.name || ('#' + selectedCredential.id)),
            selectedCredential.supports_cloud_init_ssh_key === true
              ? 'klucz publiczny: automatycznie z klucza prywatnego'
              : (selectedCredential.supports_cloud_init_password === true
                  ? 'hasło: pobierane bez ujawniania w przeglądarce'
                  : 'metoda uwierzytelnienia: SSH'),
          ].join(' · ') }))
      : null;

    let manualAccess = null;
    if (state.guestAccountMode !== 'existing_template') {
      const usernameField = field('Użytkownik SSH', 'cloud_init_ssh_username', {
        value: selectedCredential?.username || state.sshUsername || 'clouduser',
        help: selectedCredential
          ? 'Username jest pobierany z wybranego Dostępu i ma pierwszeństwo przed wartością ręczną.'
          : 'Konto zostanie utworzone lub skonfigurowane przez Cloud-init.',
      });
      const usernameInput = usernameField.querySelector('input');
      usernameInput.disabled = Boolean(selectedCredential);
      usernameInput.addEventListener('input', event => {
        if (!event.currentTarget.disabled) state.sshUsername = event.currentTarget.value;
      });

      const passwordField = field('Hasło SSH', 'cloud_init_ssh_password', {
        type: 'password',
        value: state.sshPassword || '',
        wide: true,
        minlength: 12,
        maxlength: 256,
        autocomplete: 'new-password',
        placeholder: state.managedGuestCredentialId ? '•••••••••••• (zapisane)' : 'Minimum 12 znaków',
        help: selectedCredential
          ? 'Hasło jest pobierane z wybranego Dostępu. Ręczna wartość nie jest używana.'
          : (state.managedGuestCredentialId
              ? 'Pozostaw puste, aby zachować zapisane hasło. Nowa wartość zastąpi sekret w zaszyfrowanym Dostępie SSH.'
              : 'Opcjonalne. Hasło zostanie zapisane jako zaszyfrowany Dostęp SSH; Blueprint zachowa wyłącznie jego ID.'),
      });
      const passwordInput = passwordField.querySelector('input');
      passwordInput.disabled = Boolean(selectedCredential);
      passwordInput.addEventListener('input', event => {
        if (event.currentTarget.disabled) return;
        state.sshPassword = event.currentTarget.value;
        state.guestCredentialManaged = Boolean(
          state.sshPassword || state.managedGuestCredentialId
        );
      });

      const credentialOwnsKey = selectedCredential?.supports_cloud_init_ssh_key === true;
      const publicKeyField = field('Klucz publiczny SSH', 'cloud_init_ssh_public_key', {
        tag: 'textarea',
        value: state.sshPublicKey,
        wide: true,
        help: credentialOwnsKey
          ? 'Klucz publiczny zostanie automatycznie wyprowadzony z klucza prywatnego Dostępu. Ręczna wartość nie jest używana.'
          : (selectedCredential
              ? 'Opcjonalny klucz publiczny. Przy Credentialzie hasłowym może zostać dodany obok logowania hasłem.'
              : 'Opcjonalny pojedynczy klucz publiczny OpenSSH, np. ssh-ed25519.'),
      });
      const publicKeyInput = publicKeyField.querySelector('textarea');
      publicKeyInput.disabled = credentialOwnsKey;
      publicKeyInput.addEventListener('input', event => {
        if (!event.currentTarget.disabled) state.sshPublicKey = event.currentTarget.value;
      });

      manualAccess = node('details', { class: 'advanced-options wide blueprint-wizard-cloud-init-manual' },
        node('summary', { text: selectedCredential ? 'Nadpisanie ręczne / fallback' : 'Ustawienia ręczne SSH' }),
        node('div', { class: 'advanced-options-body form-grid' },
          usernameField,
          passwordField,
          publicKeyField));
    }

    const networkSummary = node('div', { class: 'blueprint-wizard-info wide' },
      node('strong', { text: 'Sieć Cloud-init' }),
      node('span', { text: state.ipMode === 'dhcp'
        ? 'DHCP na głównym interfejsie. Szczegóły konfigurujesz w kroku „Sieć”.'
        : state.ipMode === 'ipam'
          ? 'Adres, brama i DNS zostaną pobrane z IPAM. Szczegóły konfigurujesz w kroku „Sieć”.'
          : 'Statyczny adres i DNS zostaną zapisane do network-config. Szczegóły konfigurujesz w kroku „Sieć”.' }));

    panel.append(accountMode, credentialField);
    if (credentialSummary) panel.append(credentialSummary);
    if (manualAccess) panel.append(manualAccess);
    panel.append(
      networkSummary,
      node('p', { class: 'muted wide', text: state.guestAccountMode === 'existing_template'
        ? 'Cloud-init wykona konfigurację systemu, sieci i opcjonalnie QEMU Guest Agent, ale zachowa istniejące konto z template. Wybrany Dostęp będzie używany później przez etapy wymagające dostępu do systemu gościa.'
        : 'Konfiguracja trafi na nośnik NoCloud ISO (CIDATA) przez API Proxmoxa przed uruchomieniem VM. Ręcznie wpisane hasło jest przesyłane wyłącznie podczas zapisu Blueprintu, szyfrowane w backendzie i dalej przechowywane jako ID Dostępu; jawne hasło nie trafia do zmiennych Terraform.' }),
      node('p', { class: 'muted wide', text: 'Opcja „Instaluj QEMU Guest Agent automatycznie” dodaje pakiet oraz uruchomienie usługi do Cloud-init. Nie instaluje agenta przez SSH.' }),
      node('details', { class: 'advanced-options wide' },
        node('summary', { text: 'Podgląd Cloud-init bez sekretów' }),
        node('pre', { class: 'code-block', text: preview(state, data.credentials || []) }))
    );
    return panel;
  }

  parts.cloudInit = { enabled, toggleStep, validate, preview, render };
  registerExtension('blueprint-wizard-cloud-init', () => {});
})();
