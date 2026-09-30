'use strict';

/* Server-side preference only; never an infrastructure authorization boundary. */
(() => {
  let currentScope = null;
  let currentEntityKey = null;
  let currentEntityPermissions = null;
  let currentVersion = 0;
  let revokedSelection = false;
  let directory = { tenants: [], projects: [] };
  let shellHost = null;
  let shellGeneration = 0;

  function setTopbarValue(selector, value) {
    if (typeof document === 'undefined') return;
    const target = document.querySelector(selector);
    if (!target) return;
    const text = String(value || '—');
    target.textContent = text;
    target.title = text;
  }
  function entityParts(value) {
    const parts = String(value || '').split('.');
    if (parts.length !== 4 || parts[0].toLowerCase() !== 'entity') return null;
    return { apmid: parts[1], environment: parts[2], role: parts[3] };
  }

  function entityLabel(value) {
    const parsed = entityParts(value);
    if (!parsed) return 'Brak Entity';
    return parsed.apmid + ' · ' + parsed.environment.toUpperCase() + ' · ' + parsed.role;
  }

  function renderTopbarContext(current, unavailable = false) {
    if (unavailable) {
      setTopbarValue('#current-context-organization', 'Niedostępna');
      setTopbarValue('#current-context-project', 'Niedostępny');
      setTopbarValue('#current-context-environment', '—');
      return;
    }
    const selected = current?.selected || null;
    setTopbarValue('#current-context-organization', selected ? (current.tenant_name || selected.tenant_id || '—') : 'Default');
    setTopbarValue('#current-context-project', selected?.name || 'Default');
    setTopbarValue('#current-context-environment', current?.entity_key ? entityLabel(current.entity_key) : 'Brak Entity');
  }
  async function refreshTopbarContext() {
    try {
      const current = await api('/project-context');
      renderTopbarContext(current);
      return current;
    } catch (error) {
      renderTopbarContext(null, error?.status === 404);
      if (error?.status !== 404) throw error;
      return null;
    }
  }
  function validAlways() { return true; }

  function safeAction(label, operation, valid, kind = 'ghost') {
    return button(label, async () => {
      if (!valid()) return;
      try { await operation(); } catch (error) { if (valid()) toast(error.message, 'error'); }
    }, kind);
  }

  function scopeHeaders() {
    if (!currentScope?.tenant_id || !currentScope?.id) return {};
    return {
      'X-Tenant-ID': String(currentScope.tenant_id),
      'X-Project-ID': String(currentScope.id),
      ...(currentEntityKey ? { 'X-Entity': String(currentEntityKey) } : {}),
    };
  }

  function scopeSnapshot() {
    if (!currentScope?.tenant_id || !currentScope?.id) return null;
    const tenant = directory.tenants.find(item => String(item.id) === String(currentScope.tenant_id));
    return Object.freeze({
      tenant_id: String(currentScope.tenant_id),
      tenant_name: tenant?.name || String(currentScope.tenant_id),
      tenant_slug: tenant?.slug || '',
      project_id: String(currentScope.id),
      project_name: currentScope.name || String(currentScope.id),
      project_slug: currentScope.slug || '',
      entity_key: currentEntityKey || null,
      entity: entityParts(currentEntityKey),
    });
  }

  async function fetchAll(path) {
    const items = [];
    let offset = 0;
    while (true) {
      const separator = path.includes('?') ? '&' : '?';
      const page = await api(path + separator + 'limit=200&offset=' + offset);
      const rows = Array.isArray(page?.items) ? page.items : [];
      items.push(...rows);
      offset += rows.length;
      if (!rows.length || offset >= Number(page?.total || rows.length)) break;
    }
    return items;
  }

  async function loadDirectory() {
    const [tenants, projects] = await Promise.all([
      fetchAll('/tenants'),
      fetchAll('/projects'),
    ]);
    directory = { tenants, projects };
    return directory;
  }

  async function readCurrent() {
    try {
      const current = await api('/project-context');
      currentScope = current.selected || null;
      currentEntityKey = current.entity_key || null;
      currentEntityPermissions = Array.isArray(current.entity_permissions) ? new Set(current.entity_permissions) : null;
      currentVersion = Number(current.version || 0);
      revokedSelection = false;
      renderTopbarContext(current);
      return current;
    } catch (error) {
      if (error.status !== 404) throw error;
      currentScope = null;
      currentEntityKey = null;
      currentEntityPermissions = null;
      currentVersion = 0;
      revokedSelection = true;
      renderTopbarContext(null, true);
      return { selected: null, version: 0, revoked: true };
    }
  }

  async function choose(item, valid = validAlways, options = {}) {
    if (!valid()) return null;
    let current;
    try {
      current = await api('/project-context');
    } catch (error) {
      if (error.status !== 404) throw error;
      // A saved project can become inaccessible after an RBAC or membership change.
      // Clearing without a version is the documented recovery path for that state.
      await api('/project-context', { method: 'DELETE' });
      if (!valid()) return null;
      current = await api('/project-context');
    }
    if (!valid()) return null;
    const result = await api('/project-context', {method: 'PUT', body: {
      tenant_id: item.tenant_id, project_id: item.id, entity_key: item.entity_key || null, expected_version: current.version,
    }});
    if (!valid()) return null;
    currentScope = result.selected || null;
    currentEntityKey = result.entity_key || null;
    currentEntityPermissions = Array.isArray(result.entity_permissions) ? new Set(result.entity_permissions) : null;
    currentVersion = Number(result.version || 0);
    revokedSelection = false;
    renderTopbarContext(result);
    renderShell();
    if (typeof emitUiEvent === 'function') emitUiEvent('scope-changed', { scope: scopeSnapshot() });
    if (options.notify !== false) toast('Kontekst pracy zapisany.');
    return result;
  }

  async function clear(valid = validAlways) {
    if (!valid()) return null;
    let current;
    try { current = await api('/project-context'); }
    catch (error) {
      if (error.status !== 404) throw error;
      current = null;
    }
    if (!valid()) return null;
    const query = current ? '?expected_version=' + encodeURIComponent(current.version) : '';
    const result = await api('/project-context' + query, { method: 'DELETE' });
    if (!valid()) return null;
    currentScope = null;
    currentEntityKey = null;
    currentEntityPermissions = null;
    currentVersion = Number(result.version || 0);
    revokedSelection = false;
    renderTopbarContext(result);
    renderShell();
    if (typeof emitUiEvent === 'function') emitUiEvent('scope-changed', { scope: null });
    return result;
  }

  async function panel(reload, valid) {
    let current, revoked = false;
    try { current = await api('/project-context'); }
    catch (error) {
      if (error.status !== 404) throw error;
      revoked = true;
      current = { selected: null, version: 0 };
    }
    if (!valid()) return null;
    const selected = current.selected || null;
    const state = revoked ? 'warning' : selected ? 'active' : 'empty';
    const title = revoked
      ? 'Zapisany projekt nie jest już dostępny'
      : selected
        ? selected.name
        : 'Nie wybrano kontekstu projektu';
    const description = revoked
      ? 'Wyczyść zapisany wybór i ustaw ponownie organizację oraz projekt.'
      : selected
        ? `Tenant: ${selected.tenant_id} · Entity: ${current.entity_key ? entityLabel(current.entity_key) : 'brak'}`
        : 'Wybierz organizację, projekt i Entity, aby ustawić główny zakres pracy w panelu.';

    const clearPanel = async () => {
      const query = revoked ? '' : `?expected_version=${current.version}`;
      const cleared = await api('/project-context' + query, {method: 'DELETE'});
      if (valid()) {
        currentScope = null;
        currentEntityKey = null;
        currentEntityPermissions = null;
        currentVersion = revoked ? 0 : Number(current.version || 0) + 1;
        revokedSelection = false;
        renderTopbarContext(cleared);
        renderShell();
        if (typeof emitUiEvent === 'function') emitUiEvent('scope-changed', { scope: null });
        await reload();
      }
    };

    const panelAction = revoked || selected
      ? safeAction('Wyczyść kontekst', clearPanel, valid)
      : safeAction('Wybierz kontekst', openPicker, valid, 'primary');

    return node('section', { class: 'projects-context-card ' + state },
      node('div', { class: 'projects-context-main' },
        node('span', { class: 'projects-context-marker', 'aria-hidden': 'true', text: selected ? 'P' : '○' }),
        node('div', { class: 'projects-context-copy' },
          node('span', { class: 'projects-context-eyebrow', text: 'Kontekst pracy' }),
          node('strong', { text: title }),
          node('p', { text: description }))),
      panelAction);
  }

  function projectOptions(tenantId) {
    return directory.projects
      .filter(item => String(item.tenant_id) === String(tenantId))
      .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'pl-PL'));
  }

  function tenantOptions() {
    const projectTenantIds = new Set(directory.projects.map(item => String(item.tenant_id)));
    return directory.tenants
      .filter(item => projectTenantIds.has(String(item.id)))
      .sort((a, b) => String(a.name || '').localeCompare(String(b.name || ''), 'pl-PL'));
  }

  function setProjectOptions(control, tenantId, preferred = '') {
    const projects = projectOptions(tenantId);
    control.setChoices(projects.map(item => ({
      value: String(item.id),
      label: item.name + (item.slug ? ' / ' + item.slug : ''),
    })), preferred);
  }

  async function reloadActiveView() {
    const target = state.routePath || state.view;
    if (target) await navigate(target);
  }

  async function openPicker() {
    const generation = ++shellGeneration;
    try {
      await Promise.all([readCurrent(), loadDirectory()]);
      if (generation !== shellGeneration) return;
      const tenants = tenantOptions();
      if (!tenants.length) {
        toast('Brak dostępnych organizacji/projektów dla bieżącego konta.', 'error');
        return;
      }

      const selectedTenantId = String(currentScope?.tenant_id || tenants[0].id);
      const organization = searchableSelectField(
        'Organizacja / Tenant',
        'tenant_id',
        tenants.map(item => ({
          value: String(item.id),
          label: item.name + (item.slug ? ' / ' + item.slug : ''),
        })),
        selectedTenantId,
        {
          required: true,
          wide: true,
          placeholder: 'Wpisz nazwę organizacji lub tenant…',
          help: 'Wpisuj kolejne znaki, aby zawężać listę dostępnych organizacji.',
        }
      );
      const project = searchableSelectField(
        'Projekt',
        'project_id',
        [],
        '',
        {
          required: true,
          wide: true,
          placeholder: 'Wpisz nazwę projektu…',
          help: 'Lista projektów jest ograniczona do wybranej organizacji i filtrowana podczas pisania.',
        }
      );
      const entity = searchableSelectField(
        'Entity',
        'entity_key',
        [],
        '',
        {
          required: true,
          wide: true,
          placeholder: 'Wpisz APMID, ENV albo rolę Entity…',
          help: 'Główny kontekst aplikacji: entity.<APMID>.<env>.<role>. Rola działa jako sufit uprawnień, nie jako nowe uprawnienie.',
        }
      );
      const organizationSelect = organization.searchableSelect;
      const projectSelect = project.searchableSelect;
      const entitySelect = entity.searchableSelect;

      async function loadEntityOptions(tenantId, projectId, preferred = '') {
        if (!tenantId || !projectId) {
          entitySelect.setChoices([], '');
          return;
        }
        const result = await api(
          '/project-context/entities?tenant_id=' + encodeURIComponent(tenantId)
          + '&project_id=' + encodeURIComponent(projectId),
          { scope: false }
        );
        const rows = Array.isArray(result?.items) ? result.items : [];
        entitySelect.setChoices(rows.map(item => ({
          value: String(item.key),
          label: String(item.key) + ' · ' + String(item.role_label || item.role || ''),
        })), preferred);
      }

      setProjectOptions(projectSelect, organizationSelect.value(), currentScope?.id || '');
      await loadEntityOptions(
        organizationSelect.value(),
        projectSelect.value(),
        currentEntityKey || ''
      );
      organizationSelect.onChange(async tenantId => {
        setProjectOptions(projectSelect, tenantId);
        await loadEntityOptions(tenantId, projectSelect.value(), '');
      });
      projectSelect.onChange(async projectId => {
        await loadEntityOptions(organizationSelect.value(), projectId, '');
      });

      const summary = node('div', { class: 'global-context-summary' },
        node('strong', { text: 'Globalny zakres pracy' }),
        node('p', { class: 'muted', text:
          'Wybór Entity jest zapisywany na koncie użytkownika. Backend weryfikuje RBAC i Policy Engine dla każdej operacji; rola Entity wyłącznie ogranicza uprawnienia.' }));

      openModal({
        title: 'Zmień kontekst pracy',
        eyebrow: 'CloudPortal / globalny scope',
        body: node('div', { class: 'stack' }, summary, node('div', { class: 'form-grid' }, organization, project, entity)),
        submitLabel: 'Ustaw kontekst',
        onSubmit: async data => {
          const tenantId = String(data.get('tenant_id') || '');
          const projectId = String(data.get('project_id') || '');
          const entityKey = String(data.get('entity_key') || '');
          const selectedProject = directory.projects.find(item =>
            String(item.id) === projectId && String(item.tenant_id) === tenantId);
          if (!selectedProject) throw new Error('Wybrany projekt nie należy do wskazanej organizacji lub nie jest już dostępny.');
          if (!entityKey) throw new Error('Wybierz Entity. Aplikacja używa Entity jako głównego kontekstu dostępu.');
          await choose({ ...selectedProject, tenant_id: tenantId, entity_key: entityKey }, validAlways, { notify: false });
          toast('Zmieniono globalny kontekst pracy.');
          await reloadActiveView();
          return false;
        },
      });
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  function renderShell() {
    if (!shellHost) return;
    const snapshot = scopeSnapshot();
    const primary = revokedSelection
      ? 'Kontekst wymaga zmiany'
      : snapshot
        ? snapshot.tenant_name + ' · ' + snapshot.project_name + ' · ' + entityLabel(snapshot.entity_key)
        : 'Wybierz organizację, projekt i Entity';
    shellHost.replaceChildren(node('button', {
      type: 'button',
      class: 'global-context-trigger' + (revokedSelection ? ' warning' : ''),
      onClick: openPicker,
      'aria-label': 'Zmień globalny kontekst pracy',
      title: 'Zmień globalny kontekst pracy',
    },
      node('span', { class: 'global-context-copy' },
        node('small', { text: 'Zakres pracy' }),
        node('strong', { text: primary })),
      node('span', { class: 'global-context-chevron', 'aria-hidden': 'true', text: '⌄' })));
  }

  function ensureShellHost() {
    if (typeof document === 'undefined') return null;
    shellHost = document.querySelector('#global-project-context');
    if (shellHost) return shellHost;
    const topbar = document.querySelector('.topbar');
    const search = document.querySelector('#global-search-open');
    if (!topbar) return null;
    shellHost = node('div', { id: 'global-project-context', class: 'global-project-context' });
    if (search) topbar.insertBefore(shellHost, search);
    else topbar.append(shellHost);
    return shellHost;
  }

  async function initializeShell() {
    ensureShellHost();
    const generation = ++shellGeneration;
    try {
      await Promise.all([readCurrent(), loadDirectory()]);
      if (generation !== shellGeneration) return;
      renderShell();
    } catch (error) {
      if (generation !== shellGeneration) return;
      currentScope = null;
      currentEntityKey = null;
      currentEntityPermissions = null;
      revokedSelection = false;
      renderTopbarContext(null);
      renderShell();
      toast('Nie udało się wczytać globalnego kontekstu pracy: ' + error.message, 'error');
    }
  }

  registerExtension('project-context', () => {
    globalThis.CPProjectContext = Object.freeze({
      choose,
      clear,
      panel,
      headers: scopeHeaders,
      current: scopeSnapshot,
      allows: permission => !currentEntityPermissions || currentEntityPermissions.has(String(permission)),
      refresh: initializeShell,
      open: openPicker,
    });
    if (typeof document !== 'undefined') {
      document.addEventListener('cloudportal:app-shown', () => initializeShell());
      document.addEventListener('cloudportal:app-hidden', () => {
        shellGeneration++;
        currentScope = null;
        currentEntityKey = null;
        currentEntityPermissions = null;
        currentVersion = 0;
        revokedSelection = false;
        renderTopbarContext(null);
        if (shellHost) shellHost.replaceChildren();
      });
    }
  });
})();
