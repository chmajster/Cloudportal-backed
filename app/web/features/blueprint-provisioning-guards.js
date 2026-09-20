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

  function syncWaitAgentControl(control, snippets) {
    if (!control) return;
    if (!snippets.length) control.checked = false;
    control.disabled = !snippets.length;
  }

  registerExtension('blueprint-provisioning-guards', () => {
    window.BlueprintProvisioningGuards = {
      keyBasedSshCredentials,
      guestCredentialChoices,
      selectSnippetStorage,
      syncWaitAgentControl,
    };
  });
})();
