'use strict';

(() => {
  const shell = dom.modal.querySelector('.modal-shell');
  const nativeShowModal = dom.modal.showModal.bind(dom.modal);
  const nativeCloseModal = dom.modal.close.bind(dom.modal);
  let activeSurface = null;
  let pendingSemanticSurface = null;
  let surfaceSequence = 0;

  function surfaceRoute(view, token) {
    const current = surfaceBaseView();
    const base = typeof window.uiRoutePathForRequest === 'function'
      ? window.uiRoutePathForRequest(current, view)
      : (typeof window.uiRoutePath === 'function' ? window.uiRoutePath(view) : '/' + view);
    return '#' + base + '/page/' + token;
  }

  function surfaceBaseView(value = location.hash.slice(1)) {
    return String(value || state.view || 'dashboard').split('/page/')[0] || 'dashboard';
  }

  function modalSurfaceOpen() {
    return Boolean(activeSurface) || dom.modal.open;
  }

  function prepareSemanticSurface({ path, returnView, label = '' } = {}) {
    pendingSemanticSurface = {
      path: String(path || location.hash.slice(1) || ''),
      returnView: String(returnView || state.view || 'dashboard'),
      label: String(label || ''),
    };
  }

  function clearSemanticSurface() {
    pendingSemanticSurface = null;
  }

  function cleanupRuntime() {
    if (typeof stopTaskPolling === 'function') stopTaskPolling();
    if (state.consoleRfb) {
      try { state.consoleRfb.disconnect(); } catch { /* Session may already be disconnected. */ }
      state.consoleRfb = null;
    }
  }

  function restoreShell() {
    if (shell.parentNode !== dom.modal) dom.modal.append(shell);
  }

  function teardownSurface() {
    if (!activeSurface) return null;
    const current = activeSurface;
    cleanupRuntime();
    activeSurface = null;
    state.modalPage = null;
    if (current.page?.isConnected) current.page.remove();
    restoreShell();
    dom.modal.classList.remove('modal-console', 'modal-wide');
    return current;
  }

  function renderSurface() {
    if (dom.appView.hidden) {
      if (!dom.modal.open) nativeShowModal();
      return;
    }
    if (activeSurface) return;

    if (dom.modal.open) nativeCloseModal();

    const semantic = pendingSemanticSurface;
    pendingSemanticSurface = null;
    const returnView = semantic?.returnView || state.view;
    const route = routes.find(item => item.id === returnView);
    const token = 'surface-' + (++surfaceSequence);
    const wide = dom.modal.classList.contains('modal-wide');
    const consoleMode = dom.modal.classList.contains('modal-console');
    const back = button('← Wstecz', closeCloudportalSurface, 'ghost');
    back.classList.add('page-surface-back');
    const navigation = node('div', { class: 'page-surface-navigation' },
      back,
      node('span', {
        class: 'page-surface-parent',
        text: semantic?.label || route?.label || dom.pageTitle.textContent || 'Cloudportal',
      })
    );
    const page = node('section', {
      class: 'page-surface'
        + (wide ? ' page-surface-wide' : '')
        + (consoleMode ? ' page-surface-console' : ''),
    }, navigation, shell);

    activeSurface = { page, returnView, token, semantic: Boolean(semantic), semanticPath: semantic?.path || '' };
    state.modalPage = { returnView, token, semantic: Boolean(semantic) };
    dom.content.replaceChildren(page);
    if (!semantic) {
      history.pushState(
        { cloudportalPageSurface: true, returnView, token },
        '',
        surfaceRoute(returnView, token)
      );
    }
    dom.content.focus();
    window.setTimeout(() => shell.querySelector('input,select,textarea,button')?.focus(), 20);
  }

  function closeCloudportalSurface() {
    if (!activeSurface) {
      if (dom.modal.open) nativeCloseModal();
      return false;
    }
    if (!activeSurface.semantic
        && history.state?.cloudportalPageSurface
        && history.state.token === activeSurface.token) {
      history.back();
      return true;
    }
    const current = teardownSurface();
    if (current) navigate(current.returnView);
    return true;
  }

  function dismissCloudportalSurfaceForNavigation() {
    if (!activeSurface) return false;
    const current = teardownSurface();
    if (current && !current.semantic && history.state?.cloudportalPageSurface) {
      const path = typeof window.uiRoutePath === 'function' ? window.uiRoutePath(current.returnView) : current.returnView;
      history.replaceState(null, '', '#' + path);
    }
    return true;
  }

  function handleSurfacePopState(event) {
    if (activeSurface) {
      const sameSurface = event.state?.cloudportalPageSurface
        && event.state.token === activeSurface.token;
      if (sameSurface) return;
      const current = teardownSurface();
      const view = surfaceBaseView() || current?.returnView || 'dashboard';
      navigate(view);
      return;
    }

    if (event.state?.cloudportalPageSurface) {
      const view = event.state.returnView || surfaceBaseView();
      const path = typeof window.uiRoutePath === 'function' ? window.uiRoutePath(view) : view;
      history.replaceState(null, '', '#' + path);
      navigate(view);
    }
  }

  dom.modal.showModal = renderSurface;

  window.modalSurfaceOpen = modalSurfaceOpen;
  window.prepareSemanticSurface = prepareSemanticSurface;
  window.clearSemanticSurface = clearSemanticSurface;
  window.closeCloudportalSurface = closeCloudportalSurface;
  window.dismissCloudportalSurfaceForNavigation = dismissCloudportalSurfaceForNavigation;
  window.surfaceBaseView = surfaceBaseView;

  window.addEventListener('popstate', handleSurfacePopState);
})();
