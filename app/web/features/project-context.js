'use strict';

/* Server-side preference only; never an infrastructure authorization boundary. */
(() => {
  function setTopbarValue(selector, value) {
    if (typeof document === 'undefined') return;
    const target = document.querySelector(selector);
    if (!target) return;
    const text = String(value || '—');
    target.textContent = text;
    target.title = text;
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
    setTopbarValue('#current-context-environment', selected?.default_environment ? String(selected.default_environment).toUpperCase() : '—');
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
  function safeAction(label, operation, valid) {
    return button(label, async () => {
      if (!valid()) return;
      try { await operation(); } catch (error) { if (valid()) toast(error.message, 'error'); }
    });
  }
  async function choose(item, valid) {
    if (!valid()) return;
    const current = await api('/project-context');
    if (!valid()) return;
    const updated = await api('/project-context', {method: 'PUT', body: {
      tenant_id: item.tenant_id, project_id: item.id, expected_version: current.version,
    }});
    if (valid()) {
      renderTopbarContext(updated);
      toast('Kontekst projektu zapisany. Nie zmienia to jeszcze dostępu do infrastruktury.');
    }
  }
  async function panel(reload, valid) {
    let current, revoked = false;
    try { current = await api('/project-context'); }
    catch (error) {
      if (error.status !== 404) throw error;
      revoked = true;
    }
    if (!valid()) return null;
    const text = revoked ? 'Zapisany projekt nie jest już dostępny. Wyczyść wybór.' : current.selected
      ? `Zapisany kontekst: ${current.selected.name} / ${current.selected.tenant_id}`
      : 'Brak zapisanego kontekstu projektu.';
    const clear = async () => {
      const query = revoked ? '' : `?expected_version=${current.version}`;
      const cleared = await api('/project-context' + query, {method: 'DELETE'});
      if (valid()) {
        renderTopbarContext(cleared);
        await reload();
      }
    };
    return node('div', {class: 'projects-notice'}, node('p', {text}),
      revoked || current.selected ? safeAction('Wyczyść kontekst', clear, valid) : null);
  }
  registerExtension('project-context', () => {
    globalThis.CPProjectContext = Object.freeze({choose, panel, refresh: refreshTopbarContext});
    if (typeof document !== 'undefined') {
      document.addEventListener('cloudportal:app-shown', () => { void refreshTopbarContext().catch(() => {}); });
      document.addEventListener('cloudportal:app-hidden', () => renderTopbarContext(null));
    }
    if (typeof state !== 'undefined' && state.identity) void refreshTopbarContext().catch(() => {});
  });
})();
