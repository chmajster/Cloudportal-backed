'use strict';

(() => {
  const STEP_COUNT = 10;

  function normalizedWizardStep(value) {
    const step = Number.parseInt(value, 10);
    return Number.isFinite(step) ? Math.min(STEP_COUNT - 1, Math.max(0, step)) : 0;
  }

  function blueprintWizardPath({
    item = null,
    step = 0,
    slug = '',
    hostnameSchemeId = '',
    tenantId = '',
    projectId = '',
  } = {}) {
    const stepNumber = normalizedWizardStep(step) + 1;
    const params = new URLSearchParams();
    if (hostnameSchemeId) params.set('hostnameSchemeId', String(hostnameSchemeId));
    if (tenantId) params.set('tenantId', String(tenantId));
    if (projectId) params.set('projectId', String(projectId));
    const query = params.toString() ? '?' + params.toString() : '';
    if (!item?.id) return '/blueprints/new/step/' + stepNumber + query;
    const readableSlug = String(slug || item.slug || item.name || 'blueprint').trim() || 'blueprint';
    return '/blueprints/edit/' + encodeURIComponent(String(item.id)) + '/' + encodeURIComponent(readableSlug)
      + '/step/' + stepNumber + query;
  }

  function replaceBlueprintWizardRoute(item, state, options = {}) {
    if (options.page !== true) return;
    const path = blueprintWizardPath({
      item,
      step: state.step,
      slug: state.slug,
      hostnameSchemeId: options.hostnameSchemeId || '',
      tenantId: state.tenantId || options.tenantId || '',
      projectId: state.projectId || options.projectId || '',
    });
    state.routePath = path;
    if (location.hash.slice(1) !== path) history.replaceState(history.state, '', '#' + path);
  }

  function navigateBlueprintWizard(options = {}) {
    return navigate(blueprintWizardPath({
      item: options.item || null,
      step: options.initialStep || 0,
      slug: options.item?.slug || '',
      hostnameSchemeId: options.hostnameSchemeId || '',
      tenantId: options.tenantId || '',
      projectId: options.projectId || '',
    }));
  }

  function safeApi(path, _fallback = [], options = {}) {
    return api(path, options).then(result => result.items || result);
  }

  function optionalApi(path, fallback = [], options = {}) {
    return api(path, options).then(result => result.items || result).catch(() => fallback);
  }

  window.BlueprintWizardRouting = {
    normalizedWizardStep,
    blueprintWizardPath,
    replaceBlueprintWizardRoute,
    navigateBlueprintWizard,
    safeApi,
    optionalApi,
  };

  registerExtension('blueprint-wizard-routing', () => {});
})();
