'use strict';

(() => {
  const routedForms = [];

  function normalizeRoutedFormPath(value = location.hash.slice(1)) {
    const raw = String(value || '').trim();
    const withoutHash = raw.startsWith('#') ? raw.slice(1) : raw;
    const withoutSurface = withoutHash.split('/page/')[0];
    const [pathname, query = ''] = withoutSurface.split('?');
    const normalizedPath = pathname.length > 1 && pathname.endsWith('/')
      ? pathname.slice(0, -1)
      : pathname || '/';
    return {
      pathname: normalizedPath,
      query,
      fullPath: normalizedPath + (query ? '?' + query : ''),
    };
  }

  function registerRoutedForm(route, handler) {
    if (!route?.id || !(route.pattern instanceof RegExp) || !route.parent || typeof handler !== 'function') {
      throw new Error('Invalid routed form registration');
    }
    if (routedForms.some(item => item.id === route.id)) {
      throw new Error('Duplicate routed form: ' + route.id);
    }
    routedForms.push(Object.freeze({ ...route, handler }));
  }

  function matchRoutedForm(value = location.hash.slice(1)) {
    const normalized = normalizeRoutedFormPath(value);
    for (const route of routedForms) {
      route.pattern.lastIndex = 0;
      const match = route.pattern.exec(normalized.pathname);
      if (!match) continue;
      return {
        route,
        pathname: normalized.pathname,
        query: normalized.query,
        fullPath: normalized.fullPath,
        params: { ...(match.groups || {}) },
        searchParams: new URLSearchParams(normalized.query),
      };
    }
    return null;
  }

  function routedFormParent() {
    return matchRoutedForm()?.route?.parent || '';
  }

  function viewIs(viewId) {
    return state.view === viewId
      || (state.view === 'routed-form' && routedFormParent() === viewId);
  }

  window.routedFormRegistry = Object.freeze({
    routes: routedForms,
    normalize: normalizeRoutedFormPath,
    register: registerRoutedForm,
    match: matchRoutedForm,
    parent: routedFormParent,
    viewIs,
  });
  window.registerRoutedForm = registerRoutedForm;
  window.matchRoutedForm = matchRoutedForm;
  window.routedFormParent = routedFormParent;
  window.viewIs = viewIs;
})();
