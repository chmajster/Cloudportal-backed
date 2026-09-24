'use strict';

(() => {
  function keyBasedSshCredentials(credentials = []) {
    return credentials.filter(value =>
      value.type === 'ssh' && value.supports_cloud_init_ssh_key === true);
  }

  function guestSshCredentials(credentials = []) {
    return credentials.filter(value =>
      value.type === 'ssh'
      && (
        value.supports_cloud_init_ssh_key === true
        || value.supports_cloud_init_password === true
      ));
  }

  function guestCredentialChoices(credentials = []) {
    return guestSshCredentials(credentials).map(value => {
      const methods = [];
      if (value.supports_cloud_init_password === true) methods.push('hasło');
      if (value.supports_cloud_init_ssh_key === true) methods.push('klucz SSH');
      return {
        value: value.id,
        label: value.name
          + (value.username ? ' · ' + value.username : '')
          + (methods.length ? ' · ' + methods.join(' + ') : '')
          + ' (#' + value.id + ')',
      };
    });
  }

  function guestCredentialField(credentials = [], selected = '', templateId = 'proxmox-vm') {
    const field = selectField('Konto zarządzane przez Cloud-init', 'deployment_guest_credential_id',
      [{ value: '', label: 'Nie zmieniaj konta przez Cloud-init' }, ...guestCredentialChoices(credentials)],
      selected || '', { wide: true,
        help: 'Cloud-init utworzy albo zaktualizuje użytkownika z Credentiala. Klucz prywatny nie jest kopiowany do VM; wykorzystywany jest wyłącznie odpowiadający mu klucz publiczny.' });
    const sync = value => {
      const supported = value === 'proxmox-vm';
      field.hidden = !supported;
      if (!supported) field.querySelector('select').value = '';
    };
    sync(templateId);
    return { field, sync };
  }

  function templateGuestCredentialField(credentials = [], selected = '', templateId = 'proxmox-vm') {
    const field = selectField('Istniejące konto lokalne w template', 'deployment_template_guest_credential_id',
      [{ value: '', label: 'Nie używaj predefiniowanego konta z template' }, ...guestCredentialChoices(credentials)],
      selected || '', { wide: true,
        help: 'Credential opisuje konto, które już istnieje w bazowej VM/template. Cloudportal go nie tworzy. Konto jest używane jako domyślny dostęp SSH dla późniejszych kroków workflow.' });
    const sync = value => {
      const supported = value === 'proxmox-vm';
      field.hidden = !supported;
      if (!supported) field.querySelector('select').value = '';
    };
    sync(templateId);
    return { field, sync };
  }

  function selectSnippetStorage(storages = [], current = '') {
    const available = storages.filter(value => !value.disable);
    const snippets = available.filter(value => String(value.content || '').includes('snippets'));
    const selected = snippets.find(value => String(value.storage || value.id) === String(current))
      || snippets.find(value => String(value.storage || value.id) === 'local')
      || snippets[0]
      || null;
    return {
      snippets,
      storage: selected ? String(selected.storage || selected.id) : '',
    };
  }

  function syncQemuGuestAgentInstallControl(control, snippets, readiness = { ok: true }) {
    if (!control) return;
    const snippetsAvailable = snippets.length > 0;
    if (!snippetsAvailable) control.checked = false;
    control.disabled = !snippetsAvailable;
    control.title = !snippetsAvailable
      ? 'Brak storage z obsługą snippets'
      : readiness?.ok === false
        ? 'Preflight SSH Proxmox nieudany: ' + (readiness.reason || 'ssh_not_ready')
          + '. Ustawienie można zapisać, ale wykonanie Blueprintu wymaga sprawnego SSH.'
        : '';
  }

  function syncWaitAgentControl(control, snippets, readiness = { ok: true }) {
    syncQemuGuestAgentInstallControl(control, snippets, readiness);
  }

  function workflowChoicesForProvider(workflowTypes, provider, currentType = '') {
    const proxmoxOnly = new Set(['cloud_init', 'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
      'run_ansible_playbook', 'create_snapshot', 'health_check']);
    const available = provider === 'proxmox'
      ? workflowTypes
      : workflowTypes.filter(([value]) => !proxmoxOnly.has(value));
    return available.some(([value]) => value === currentType) || !currentType
      ? available
      : [[currentType, 'Legacy / niedostępne dla ' + provider + ': ' + currentType], ...available];
  }

  function requiredExecutionPermissions(item = {}) {
    const required = new Set([
      'blueprints.execute',
      'jobs.execute',
      'terraform.execute',
      'deployments.create',
    ]);
    if (item.recovery_policy === 'destroy_on_failure') required.add('deployments.destroy');
    if (item.deployment?.ansible) required.add('ansible.execute');
    const stepTypes = new Set((item.workflow || []).map(step => String(step?.type || '')));
    if (stepTypes.has('run_ansible_playbook')) required.add('ansible.execute');
    if (stepTypes.has('create_snapshot')) required.add('snapshots.create');
    if (stepTypes.has('release_ip')) required.add('ipam.release');
    if (stepTypes.has('terraform_destroy')) required.add('deployments.destroy');
    return [...required].sort();
  }

  function missingExecutionPermissions(item = {}) {
    return requiredExecutionPermissions(item).filter(permission => !allowed(permission));
  }

  function workflowNeedsTags(manualTags = [], deployment = {}) {
    return Boolean(
      manualTags.length
      || deployment.apmid
      || deployment.environment
      || deployment.select_apmid_on_execute
      || deployment.select_environment_on_execute
    );
  }

  function executionControl(item, onExecute) {
    const missing = missingExecutionPermissions(item);
    if (!item.is_active || !item.visibility?.backend) return null;
    if (!missing.length) return button('Uruchom', onExecute, 'primary');
    return node('span', {
      class: 'badge warning',
      title: 'Brak uprawnień: ' + missing.join(', '),
      text: 'Brak uprawnień do uruchomienia',
    });
  }

  registerExtension('blueprint-provisioning-guards', () => {
    window.BlueprintProvisioningGuards = {
      keyBasedSshCredentials,
      guestSshCredentials,
      guestCredentialChoices,
      guestCredentialField,
      templateGuestCredentialField,
      selectSnippetStorage,
      syncQemuGuestAgentInstallControl,
      syncWaitAgentControl,
      workflowChoicesForProvider,
      requiredExecutionPermissions,
      missingExecutionPermissions,
      workflowNeedsTags,
      executionControl,
    };
  });
})();
