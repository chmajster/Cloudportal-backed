'use strict';

(() => {
  async function routedFormView() {
    const match = window.matchRoutedForm?.();
    if (!match) {
      await navigate('dashboard');
      return;
    }
    if (match.route.permission && !allowed(match.route.permission)) {
      throw new Error('Brak uprawnienia do otwarcia tego formularza.');
    }

    const useSurface = match.route.surface !== false;
    if (useSurface && typeof window.prepareSemanticSurface === 'function') {
      window.prepareSemanticSurface({
        path: match.fullPath,
        returnView: match.route.parent,
        label: match.route.label || '',
      });
    }

    try {
      await match.route.handler(match);
      if (useSurface
          && typeof window.modalSurfaceOpen === 'function'
          && !window.modalSurfaceOpen()) {
        if (typeof window.clearSemanticSurface === 'function') window.clearSemanticSurface();
        await navigate(match.route.parent);
      }
    } catch (error) {
      if (typeof window.clearSemanticSurface === 'function') window.clearSemanticSurface();
      throw error;
    }
  }

  registerView({
    id: 'routed-form',
    label: 'Formularz',
    permission: null,
    order: 998,
    navigation: false,
  }, routedFormView);
})();
