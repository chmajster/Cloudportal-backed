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
      };
    }
    if (editMatch) {
      return {
        mode: 'edit',
        id: Number(editMatch[1]),
        slug: editMatch[2] ? decodeURIComponent(editMatch[2]) : '',
        step: Number(editMatch[3]),
        hostnameSchemeId: params.get('hostnameSchemeId') || '',
      };
    }
    return null;
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
    if (route.mode === 'edit') {
      if (!allowed('blueprints.update')) throw new Error('Brak uprawnienia do edycji Blueprintów.');
      item = await api('/blueprints/' + route.id);
      if (!window.BlueprintsFeature?.canManage?.(item)) {
        throw new Error('Brak roli zarządzającej tym Blueprintem.');
      }
    } else if (!allowed('blueprints.create')) {
      throw new Error('Brak uprawnienia do tworzenia Blueprintów.');
    }

    await window.BlueprintWizard.render({
      item,
      page: true,
      initialStep: route.step - 1,
      hostnameSchemeId: route.hostnameSchemeId || undefined,
    });
  }

  async function executeById(id) {
    if (!window.BlueprintsFeature?.execute) {
      throw new Error('Obsługa wykonywania Blueprintu nie została załadowana.');
    }
    const item = await api('/blueprints/' + id);
    await window.BlueprintsFeature.execute(item);
  }

  registerRoutedForm({
    id: 'blueprints-execute',
    pattern: /^\/blueprints\/(?<id>\d+)(?:\/[^/]+)?\/execute$/,
    parent: 'blueprints',
    permission: 'blueprints.execute',
    label: 'Blueprinty',
  }, match => executeById(match.params.id));

  registerRoutedForm({
    id: 'products-blueprint-create',
    pattern: /^\/products\/(?<id>\d+)(?:\/[^/]+)?\/create$/,
    parent: 'deployments',
    permission: 'blueprints.execute',
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
