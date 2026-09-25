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

  async function blueprintScopes(permission) {
    const result = await api('/blueprints/creation-scopes?permission=' + encodeURIComponent(permission) + '&limit=200');
    return result.items || [];
  }

  function preferredScope(scopes, route = {}) {
    const remembered = window.CloudportalBlueprintScope || {};
    return scopes.find(scope =>
      route.tenantId
      && route.projectId
      && String(scope.tenant_id) === String(route.tenantId)
      && String(scope.project_id) === String(route.projectId)
    ) || scopes.find(scope =>
      remembered.tenant_id
      && remembered.project_id
      && String(scope.tenant_id) === String(remembered.tenant_id)
      && String(scope.project_id) === String(remembered.project_id)
    ) || scopes[0] || null;
  }

  function rememberScope(scope) {
    if (!scope) return;
    window.CloudportalBlueprintScope = {
      tenant_id: String(scope.tenant_id),
      project_id: String(scope.project_id),
    };
  }

  async function resolveBlueprint(id, permission, route = {}) {
    const scopes = await blueprintScopes(permission);
    const preferred = preferredScope(scopes, route);
    const ordered = preferred
      ? [preferred, ...scopes.filter(scope => scope !== preferred)]
      : scopes;
    for (const scope of ordered) {
      if (!(scope.permissions || []).includes('blueprints.read') && !allowed('blueprints.read')) continue;
      const result = await api('/blueprints/' + id, {
        headers: scopeHeaders(scope),
        allow: [403, 404],
      });
      if (result && Number(result.id) === Number(id)) {
        rememberScope(scope);
        return { item: result, scope };
      }
    }
    throw new Error('Blueprint nie istnieje albo nie masz dostępu w żadnym projekcie.');
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
    let scope = null;
    if (route.mode === 'edit') {
      const resolved = await resolveBlueprint(route.id, 'blueprints.update', route);
      item = resolved.item;
      scope = resolved.scope;
      if (!window.BlueprintsFeature?.canManage?.(item)) {
        throw new Error('Brak roli zarządzającej tym Blueprintem.');
      }
    } else {
      const scopes = await blueprintScopes('blueprints.create');
      scope = preferredScope(scopes, route);
      if (!scope) throw new Error('Brak projektu z uprawnieniem blueprints.create.');
      rememberScope(scope);
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

  async function executeById(id) {
    if (!window.BlueprintsFeature?.execute) {
      throw new Error('Obsługa wykonywania Blueprintu nie została załadowana.');
    }
    const resolved = await resolveBlueprint(id, 'blueprints.execute');
    await window.BlueprintsFeature.execute(resolved.item, resolved.scope);
  }

  registerRoutedForm({
    id: 'blueprints-execute',
    pattern: /^\/blueprints\/(?<id>\d+)(?:\/[^/]+)?\/execute$/,
    parent: 'blueprints',
    permission: null,
    label: 'Blueprinty',
  }, match => executeById(match.params.id));

  registerRoutedForm({
    id: 'products-blueprint-create',
    pattern: /^\/products\/(?<id>\d+)(?:\/[^/]+)?\/create$/,
    parent: 'deployments',
    permission: null,
    label: 'Produkty',
  }, match => executeById(match.params.id));

  registerView({
    id: 'blueprint-wizard',
    label: 'Kreator Blueprintu',
    permission: null,
    order: 71,
    navigation: false,
    navigationParent: 'blueprints',
  }, blueprintWizardView);
})();
