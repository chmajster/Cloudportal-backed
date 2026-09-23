'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function selected(state) {
    return state.providerType === 'proxmox'
      && (!state.advancedWorkflow || state.workflow.some(step => step.type === 'cloud_init'));
  }

  function preview(state, credentials = []) {
    const credential = credentials.find(row => String(row.id) === String(state.guestCredentialId));
    const username = credential?.username || state.sshUsername || 'clouduser';
    const lines = [
      '#cloud-config',
      '# Konto i sieć wygeneruje natywny cloud-init Proxmox.',
      '# Użytkownik: ' + JSON.stringify(username),
      '# Dane dostępowe: ' + (credential ? '#' + credential.id : 'ręczny login / klucz publiczny'),
    ];
    if (credential?.supports_cloud_init_password) {
      lines.push('# Hasło: pobierane przy wykonaniu; nigdy nie jest wyświetlane w podglądzie.');
    }
    if (credential?.supports_cloud_init_ssh_key) {
      lines.push('# SSH: wyłącznie klucz publiczny wyliczony z credentiala; bez klucza prywatnego.');
    }
    if (state.installQemuGuestAgent) {
      lines.push(
        'package_update: true',
        'packages:',
        '  - qemu-guest-agent',
        'runcmd:',
        '  - |',
        '    set -eu',
        '    if command -v systemctl >/dev/null 2>&1; then',
        '      systemctl enable qemu-guest-agent || true',
        '      systemctl start qemu-guest-agent',
        '      systemctl is-active --quiet qemu-guest-agent',
        '    elif command -v rc-service >/dev/null 2>&1; then',
        '      rc-update add qemu-guest-agent default',
        '      rc-service qemu-guest-agent start',
        '    else',
        '      echo "Unsupported service manager for qemu-guest-agent" >&2',
        '      exit 1',
        '    fi',
      );
    } else {
      lines.push('# Automatyczna instalacja QEMU Guest Agent jest wyłączona.');
    }
    return lines.join('\n') + '\n';
  }

  function addStep(state) {
    const steps = state.workflow;
    if (steps.some(step => step.type === 'cloud_init')) return;
    const ids = new Set(steps.map(step => step.id));
    let id = 'cloud_init';
    for (let suffix = 2; ids.has(id); suffix += 1) id = 'cloud_init_' + suffix;
    steps.unshift({ id, type: 'cloud_init', depends_on: [], conditions: {}, retry: 0, timeout: 600, rollback: null });
    for (const step of steps) {
      if (['terraform_plan', 'terraform_apply'].includes(step.type)) {
        step.depends_on = [...new Set([...(step.depends_on || []), id])];
      }
    }
  }

  function validate(state) {
    const errors = {};
    if (!selected(state)) return errors;
    if (state.installQemuGuestAgent && !state.cloudInitSnippetStorage) {
      errors.cloud_init_snippet_storage = 'Instalacja QEMU Guest Agent przez Cloud-init wymaga storage z obsługą snippets.';
    }
    if (!state.advancedWorkflow) return errors;
    const cloud = state.workflow.filter(step => step.type === 'cloud_init');
    if (cloud.length !== 1) {
      errors.workflow = 'Workflow może zawierać jeden krok Cloud-init.';
      return errors;
    }
    if (Object.keys(cloud[0].conditions || {}).length || cloud[0].retry || cloud[0].rollback) {
      errors.workflow = 'Cloud-init jest konfiguracją przed startem VM: bez Conditions, Retry i Rollback.';
    }
    const graph = new Map(state.workflow.map(step => [step.id, step.depends_on || []]));
    const ancestors = id => {
      const found = new Set();
      const pending = [...(graph.get(id) || [])];
      while (pending.length) {
        const parent = pending.pop();
        if (found.has(parent)) continue;
        found.add(parent);
        pending.push(...(graph.get(parent) || []));
      }
      return found;
    };
    for (const step of state.workflow) {
      if (['terraform_plan', 'terraform_apply'].includes(step.type) && !ancestors(step.id).has(cloud[0].id)) {
        errors.workflow = 'Terraform Plan i Terraform Apply muszą zależeć od kroku Cloud-init.';
      }
    }
    return errors;
  }

  function render({ state, data, capture, rerender }) {
    const content = node('section', { class: 'blueprint-wizard-inline-panel blueprint-cloud-init-panel' },
      node('h4', { text: 'Cloud-init: pierwszy start systemu' }),
      node('p', { class: 'muted', text: 'Konto docelowe jest tworzone przy pierwszym starcie VM. Zaznaczenie instalacji QEMU Guest Agent dodaje pakiet oraz uruchomienie usługi do vendor-data. Nie wymaga to SSH do gościa ani znajomości jego adresu DHCP.' }));
    const choices = window.BlueprintProvisioningGuards.guestCredentialChoices(data.credentials);
    const account = selectField('Konto Cloud-init z Danych dostępowych', 'guest_credential_id', [
      { value: '', label: 'Ręczny użytkownik i klucz publiczny' }, ...choices,
    ], state.guestCredentialId || '', { wide: true,
      help: 'Wybierz credential SSH z hasłem, kluczem lub oboma. Workflow zapisuje tylko ID. To dane konta w VM, a nie logowania do węzła Proxmox.' });
    account.querySelector('select').addEventListener('change', event => {
      capture();
      state.guestCredentialId = event.currentTarget.value;
      const credential = data.credentials.find(row => String(row.id) === String(state.guestCredentialId));
      if (credential?.username) state.sshUsername = credential.username;
      rerender();
    });
    content.append(account);
    if (!state.guestCredentialId) {
      const manual = node('div', { class: 'form-grid' },
        field('Użytkownik Cloud-init', 'ssh_username', { value: state.sshUsername || 'clouduser' }),
        field('Klucz publiczny SSH', 'ssh_public_key', { tag: 'textarea', value: state.sshPublicKey || '', wide: true }));
      manual.querySelectorAll('input,textarea').forEach(control => control.addEventListener('input', () => {
        state[control.name === 'ssh_username' ? 'sshUsername' : 'sshPublicKey'] = control.value;
        content.querySelector('[data-cloud-init-preview]').textContent = preview(state, data.credentials);
      }));
      content.append(manual);
    }
    if (state.installQemuGuestAgent) {
      const storage = selectField('Storage vendor-data (snippets)', 'cloud_init_snippet_storage',
        [{ value: '', label: 'Wybierz storage z obsługą snippets' }, ...state.snippetStorages.map(row => ({
          value: row.storage || row.id, label: row.storage || row.id,
        }))], state.cloudInitSnippetStorage || '', { wide: true });
      storage.querySelector('select').addEventListener('change', event => {
        capture();
        state.cloudInitSnippetStorage = event.currentTarget.value;
        rerender();
      });
      content.append(storage);
      if (!state.cloudInitSnippetStorage || state.qemuAgentSshReady === false) {
        content.append(node('div', { class: 'callout warning' },
          node('strong', { text: 'Wymagania uploadu Cloud-init' }),
          node('p', { text: 'Włącz snippets na wybranym storage i skonfiguruj SSH do węzła Proxmox w środowisku workera. Token API nie jest hasłem SSH. Preflight zatrzyma wykonanie przed utworzeniem VM; nie przełączy go na konto bootstrapowe gościa. Status: '
            + (state.qemuAgentSshReason || (!state.cloudInitSnippetStorage ? 'brak snippets' : 'do sprawdzenia')) })));
      }
    }
    if (state.advancedWorkflow && !selected(state)) {
      content.append(node('div', { class: 'callout warning' },
        node('p', { text: 'W tym workflow brakuje kroku Cloud-init. Bez niego pozostaje starszy tryb provisioning.' }),
        button('Dodaj Cloud-init przed Terraform', () => { capture(); addStep(state); rerender(); }, 'primary')));
    }
    content.append(node('details', { class: 'advanced-options' },
      node('summary', { text: 'Podgląd vendor-data i konfiguracji konta (bez sekretów)' }),
      node('pre', { class: 'blueprint-cloud-init-preview', 'data-cloud-init-preview': '', text: preview(state, data.credentials) })));
    return content;
  }

  parts.cloudInit = { selected, preview, addStep, validate, render };
  registerExtension('blueprint-wizard-cloud-init', () => {});
})();
