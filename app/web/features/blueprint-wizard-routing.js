'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function normalizedStep(value, total = 10) {
    const step = Number.parseInt(value, 10);
    if (!Number.isFinite(step)) return 0;
    return Math.min(Math.max(0, Number(total) - 1), Math.max(0, step));
  }

  function path({ item = null, step = 0, slug = '', hostnameSchemeId = '', total = 10 } = {}) {
    const stepNumber = normalizedStep(step, total) + 1;
    const query = hostnameSchemeId
      ? '?hostnameSchemeId=' + encodeURIComponent(String(hostnameSchemeId))
      : '';
    if (!item?.id) return '/blueprints/new/step/' + stepNumber + query;
    const readableSlug = String(slug || item.slug || item.name || 'blueprint').trim() || 'blueprint';
    return '/blueprints/edit/' + encodeURIComponent(String(item.id))
      + '/' + encodeURIComponent(readableSlug)
      + '/step/' + stepNumber
      + query;
  }

  function replaceRoute(item, state, options = {}, total = 10) {
    if (options.page !== true) return;
    const target = path({
      item,
      step: state.step,
      slug: state.slug,
      hostnameSchemeId: options.hostnameSchemeId || '',
      total,
    });
    if (location.hash.slice(1) !== target) {
      history.replaceState(history.state, '', '#' + target);
    }
  }

  function navigateWizard(options = {}, total = 10) {
    return navigate(path({
      item: options.item || null,
      step: options.initialStep || 0,
      slug: options.item?.slug || '',
      hostnameSchemeId: options.hostnameSchemeId || '',
      total,
    }));
  }

  function syncPage(item, state, options = {}, total = 10) {
    if (options.page !== true) return;
    replaceRoute(item, state, options, total);
    dom.pageTitle.textContent = item
      ? ('Edytuj Blueprint · ' + (state.name || item.name))
      : 'Nowy Blueprint';
    dom.pageEyebrow.textContent = 'Blueprinty · krok ' + (state.step + 1) + ' z ' + total;
  }

  function mount(item, state, options, shell) {
    if (options.page === true) {
      const footer = node('div', { class: 'blueprint-wizard-page-actions' });
      const pageHeader = node('div', { class: 'blueprint-wizard-page-toolbar' },
        button('← Blueprinty', () => navigate('blueprints'), 'ghost'),
        node('div', { class: 'blueprint-wizard-page-context' },
          node('strong', { text: item ? 'Edycja wersjonowanego Blueprintu' : 'Tworzenie nowego Blueprintu' }),
          node('span', { class: 'muted', text: 'Kreator działa jako osobna strona, a każdy krok ma własny adres URL.' })));
      dom.content.replaceChildren(
        node('section', { class: 'panel blueprint-wizard-page' }, pageHeader, shell, footer)
      );
      return footer;
    }

    dom.modal.classList.add('modal-wide');
    dom.modalTitle.textContent = item ? ('Edytuj Blueprint · ' + item.name) : 'Nowy Blueprint';
    dom.modalEyebrow.textContent = item ? 'Kreator krok po kroku · edycja' : 'Kreator krok po kroku';
    dom.modalBody.replaceChildren(shell);
    if (!dom.modal.open) dom.modal.showModal();
    return dom.modalActions;
  }

  parts.routing = Object.freeze({
    normalizedStep,
    path,
    replaceRoute,
    navigateWizard,
    syncPage,
    mount,
  });
  registerExtension('blueprint-wizard-routing', () => {});
})();
