'use strict';

(() => {
  function keyBasedSshCredentials(credentials = []) {
    return credentials.filter(value =>
      value.type === 'ssh' && value.supports_cloud_init_ssh_key === true);
  }

  function guestCredentialChoices(credentials = []) {
    return keyBasedSshCredentials(credentials).map(value => ({
      value: value.id,
      label: value.name + (value.username ? ' · ' + value.username : '') + ' (#' + value.id + ')',
    }));
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

  function syncWaitAgentControl(control, snippets, readiness = { ok: true }) {
    if (!control) return;
    const available = snippets.length > 0 && readiness?.ok !== false;
    if (!available) control.checked = false;
    control.disabled = !available;
    control.title = !snippets.length
      ? 'Brak storage z obsługą snippets'
      : readiness?.ok === false
        ? 'Preflight SSH Proxmox nieudany: ' + (readiness.reason || 'ssh_not_ready')
        : '';
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
      guestCredentialChoices,
      selectSnippetStorage,
      syncWaitAgentControl,
      requiredExecutionPermissions,
      missingExecutionPermissions,
      workflowNeedsTags,
      executionControl,
    };
  });
})();
