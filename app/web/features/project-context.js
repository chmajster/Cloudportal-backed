'use strict';

/* Server-side preference only; never an infrastructure authorization boundary. */
(() => {
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
    await api('/project-context', {method: 'PUT', body: {
      tenant_id: item.tenant_id, project_id: item.id, expected_version: current.version,
    }});
    if (valid()) toast('Kontekst projektu zapisany. Nie zmienia to jeszcze dostępu do infrastruktury.');
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
      await api('/project-context' + query, {method: 'DELETE'});
      if (valid()) await reload();
    };
    return node('div', {class: 'projects-notice'}, node('p', {text}),
      revoked || current.selected ? safeAction('Wyczyść kontekst', clear, valid) : null);
  }
  registerExtension('project-context', () => {
    globalThis.CPProjectContext = Object.freeze({choose, panel});
  });
})();
