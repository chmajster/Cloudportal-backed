'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function renderReview({ state, data, currentAutoWorkflow }) {
    const provider = data.providers.find(value => String(value.id) === String(state.providerId));
    const scheme = data.schemes.find(value => String(value.id) === String(state.hostnameSchemeId));
    const pool = data.pools.find(value => String(value.id) === String(state.ipamPoolId));
    const ansibleRunNames = (state.ansibleRuns || []).map(run =>
      data.playbooks.find(value => value.id === run.playbook)?.name || run.playbook
    );
    const steps = state.advancedWorkflow ? state.workflow : currentAutoWorkflow();
    const roleNames = state.allowedRoleIds.map(id => data.roles.find(value => Number(value.id) === Number(id))?.name).filter(Boolean);
    const userNames = state.allowedUserIds.map(id => data.users.find(value => Number(value.id) === Number(id))?.username).filter(Boolean);
    const entityNames = (state.allowedEntities || []).map(String);
  
    const sections = [
      ['Blueprint', [
        ['Nazwa', state.name],
        ['Slug', state.slug],
        ['Organizacja', blueprintScope.tenantLabel(state.tenantId)],
        ['Projekt', blueprintScope.projectLabel(state.projectId)],
        ['Opis', state.description || '—'],
      ]],
      ['Platforma', [
        ['Provider', provider?.name || '—'],
        ['Node', state.providerType === 'proxmox' ? state.node : '—'],
        ['Template', state.providerType === 'proxmox'
          ? ((state.selectedTemplateName || 'Template') + ' · VMID ' + state.selectedTemplateVmid)
          : (data.templates.find(value => value.id === state.terraformTemplateId)?.name || '—')],
      ]],
      ['VM', state.providerType === 'proxmox' ? [
        ['CPU', state.cpu],
        ['RAM', (Number(state.memory) / 1024) + ' GB'],
        ['Dysk', state.disk + ' GB'],
        ['Storage', state.storage],
        ['Network', state.network],
        ['Environment', state.selectEnvironmentOnExecute
          ? 'Wybierany podczas tworzenia VM'
          : (state.environment ? state.environment.toUpperCase() : '—')],
        ['Environment przy tworzeniu VM', state.selectEnvironmentOnExecute ? 'Wybierany przez użytkownika' : 'Stały z Blueprintu'],
        ['APMID', state.selectApmidOnExecute ? 'Wybierany podczas tworzenia VM' : (state.apmid || '—')],
        ['APMID przy tworzeniu VM', state.selectApmidOnExecute ? 'Wybierany przez użytkownika' : 'Stały z Blueprintu'],
        ['Cloud-init', parts.cloudInit.enabled(state) ? 'NoCloud ISO przez API; konfiguracja przy pierwszym starcie' : 'Starszy tryb'],
        ['Konto istniejące w template', state.templateGuestCredentialId
          ? (data.credentials.find(value => String(value.id) === String(state.templateGuestCredentialId))?.name || ('#' + state.templateGuestCredentialId))
          : 'Brak'],
        ['Konto zarządzane przez Cloud-init', state.guestCredentialId
          ? (data.credentials.find(value => String(value.id) === String(state.guestCredentialId))?.name || ('#' + state.guestCredentialId))
          : 'Brak'],
        ['QEMU Guest Agent', state.installQemuGuestAgent ? (state.waitAgent ? 'Instalacja przez cloud-init + oczekiwanie' : 'Instalacja przez cloud-init, bez oczekiwania') : (state.waitAgent ? 'Bez instalacji, oczekiwanie na agenta z template' : 'Wyłączony')],
        ['Klasyfikacja', state.selectApmidOnExecute || state.selectEnvironmentOnExecute
          ? 'Wyliczana podczas tworzenia VM'
          : (state.apmid && state.environment ? state.apmid + '.' + state.environment.toUpperCase() : '—')],
      ] : [
        ['Szablon IaC', state.terraformTemplateId],
        ['Parametry', Object.keys(state.genericVariables).length + ' ustawionych'],
      ]],
      ['Hostname', [
        ['Tryb', state.hostnameEnabled ? 'Automatyczny' : 'Stała nazwa'],
        ['Pattern', state.hostnameEnabled ? (scheme?.pattern || '—') : state.manualVmName],
        ['Podgląd', state.hostnameEnabled ? parts.core.hostnameExample(scheme, state.hostnameValues) : state.manualVmName],
      ]],
      ['Sieć', [
        ['Tryb', state.ipMode === 'dhcp' ? 'DHCP' : state.ipMode === 'ipam' ? 'IPAM' : 'Static'],
        ['Pula / adres', state.ipMode === 'ipam' ? (pool?.name || '—') : state.ipMode === 'static' ? state.ipv4Address : 'DHCP'],
      ]],
      ['Konfiguracja', [
        ['Ansible', state.ansibleEnabled ? (ansibleRunNames.join(' → ') || '—') : 'Brak'],
      ]],
      ['AWX', parts.awx.summaryRows(state, data)],
      ['Dostęp', [
        ['Entity', entityNames.join(', ') || 'Bez ograniczenia Entity'],
        ['Role', roleNames.join(', ') || 'Bez ograniczenia'],
        ['Użytkownicy', userNames.join(', ') || 'Bez ograniczenia'],
        ['Approval', state.requiresApproval ? 'Wymagany' : 'Nie'],
        ...window.BlueprintApprovalPolicyUI.summaryRows(state),
      ]],
    ];
  
    const content = node('div', { class: 'blueprint-wizard-review' });
    sections.forEach(([title, rows]) => {
      content.append(node('section', { class: 'blueprint-wizard-review-section' },
        node('h4', { text: title }),
        node('div', { class: 'blueprint-wizard-review-grid' },
          ...rows.map(([label, value]) => parts.ui.summaryRow(label, value)))));
    });
    content.append(node('section', { class: 'blueprint-wizard-review-section' },
      node('h4', { text: 'Workflow' }),
      parts.ui.workflowVisual(steps)));
    return content;
  }

  parts.review = Object.freeze({ render: renderReview });
  registerExtension('blueprint-wizard-review', () => {});
})();
