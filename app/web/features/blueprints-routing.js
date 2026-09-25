'use strict';

(() => {
  function blueprintWizardRouteFromLocation() {
    const raw = String(location.hash.slice(1) || '').split('/page/')[0];
    const [pathname, query = ''] = raw.split('?');
    const createMatch = pathname.match(/^\/blueprints\/new\/step\/([1-9]\d*)$/);
    const editMatch = pathname.match(/^\/blueprints\/edit\/(\d+)(?:\/([^/]+))?\/step\/([1-9]\d*)$/);
    const params = new URLSearchParams(query);

    if (createMatch) {
      return {
        mode: 'create',
        step: Number(createMatch[1]),
        hostnameSchemeId: params.get('hostnameSchemeId') || '',
        tenantId: params.get('tenantId') || '',
        projectId: params.get('projectId') || '',
      };
    }
    if (editMatch) {
      return {
        mode: 'edit',
        id: Number(editMatch[1]),
        slug: editMatch[2] ? decodeURIComponent(editMatch[2]) : '',
        step: Number(editMatch[3]),
        hostnameSchemeId: params.get('hostnameSchemeId') || '',
        tenantId: params.get('tenantId') || '',
        projectId: params.get('projectId') || '',
      };
    }
    return null;
  }

  function scopeHeaders(scope) {
    return {
      'X-Tenant-ID': String(scope.tenant_id),
      'X-Project-ID': String(scope.project_id),
    };
  }

  async function scopesFor(permission) {
    const result = await api('/blueprints/creation-scopes?permission=' + encodeURIComponent(permission) + '&limit=200');
    return result.items || [];
  }

  function preferredScopes(scopes, tenantId = '', projectId = '') {
    const exact = scopes.find(scope =>
      String(scope.tenant_id) === String(tenantId || '')
      && String(scope.project_id) === String(projectId || '')
    );
    return exact ? [exact, ...scopes.filter(scope => scope !== exact)] : scopes;
  }

  async function resolveBlueprintScope(id, permission, tenantId = '', projectId = '') {
    const scopes = preferredScopes(await scopesFor(permission), tenantId, projectId);
    if (!scopes.length) throw new Error('Brak projektu z uprawnieniem ' + permission + '.');
    for (const scope of scopes) {
      try {
        const item = await api('/blueprints/' + encodeURIComponent(id), { headers: scopeHeaders(scope) });
        return { item, scope };
      } catch (error) {
        if (![403, 404].includes(Number(error?.status))) throw error;
      }
    }
    throw new Error('Blueprint nie jest dostępny w projektach, w których masz uprawnienie ' + permission + '.');
  }

  async function resolveCreateScope(tenantId = '', projectId = '') {
    const scopes = preferredScopes(await scopesFor('blueprints.create'), tenantId, projectId);
    if (!scopes.length) throw new Error('Brak projektu z uprawnieniem blueprints.create.');
    return scopes[0];
  }

  async function blueprintWizardView() {
    const route = blueprintWizardRouteFromLocation();
    if (!route) {
      await navigate('blueprints');
      return;
    }
    if (!window.BlueprintWizard?.render) {
      throw new Error('Kreator Blueprintu nie został załadowany.');
    }

    let item = null;
    let scope;
    if (route.mode === 'edit') {
      const resolved = await resolveBlueprintScope(
        route.id,
        'blueprints.update',
        route.tenantId,
        route.projectId
      );
      item = resolved.item;
      scope = resolved.scope;
      if (!window.BlueprintsFeature?.canManage?.(item)) {
        throw new Error('Brak roli zarządzającej tym Blueprintem.');
      }
    } else {
      scope = await resolveCreateScope(route.tenantId, route.projectId);
    }

    await window.BlueprintWizard.render({
      item,
      page: true,
      initialStep: route.step - 1,
      hostnameSchemeId: route.hostnameSchemeId || undefined,
      tenantId: scope.tenant_id,
      projectId: scope.project_id,
    });
  }

  async function executeById(id, searchParams = new URLSearchParams()) {
    if (!window.BlueprintsFeature?.execute) {
      throw new Error('Obsługa wykonywania Blueprintu nie została załadowana.');
    }
    const resolved = await resolveBlueprintScope(
      id,
      'blueprints.execute',
      searchParams.get('tenantId') || '',
      searchParams.get('projectId') || ''
    );
    await window.BlueprintsFeature.execute(resolved.item, resolved.scope);
  }

  registerRoutedForm({
    id: 'blueprints-execute',
    pattern: /^\/blueprints\/(?<id>\d+)(?:\/[^/]+)?\/execute$/,
    parent: 'blueprints',
    permission: null,
    label: 'Blueprinty',
  }, match => executeById(match.params.id, match.searchParams));

  registerRoutedForm({
    id: 'products-blueprint-create',
    pattern: /^\/products\/(?<id>\d+)(?:\/[^/]+)?\/create$/,
    parent: 'deployments',
    permission: null,
    label: 'Produkty',
  }, match => executeById(match.params.id, match.searchParams));

  registerView({
    id: 'blueprint-wizard',
    label: 'Kreator Blueprintu',
    permission: null,
    order: 71,
    navigation: false,
    navigationParent: 'blueprints',
  }, blueprintWizardView);
})();
