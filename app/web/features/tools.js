'use strict';

(() => {
function toolStatus(status) {
  const map = {
    idle: ['Gotowy', ''],
    checking: ['Sprawdzanie', 'warning'],
    update_available: ['Dostępna aktualizacja', 'info'],
    running: ['Aktualizacja trwa', 'warning'],
    success: ['Ostatnia aktualizacja OK', 'ok'],
    failed: ['Wymaga uwagi', 'danger'],
    up_to_date: ['Aktualny', 'ok'],
    local_ahead: ['Lokalny commit nowszy', 'ok'],
  };
  return map[status] || ['Status nieznany', ''];
}

function toolMeta(label, value, mono = false) {
  return node('div', { class: 'tool-meta-item' },
    node('span', { text: label }),
    node('strong', { class: mono ? 'mono' : '', text: value || '—' }));
}

function autoUpdateTool(status) {
  const stateInfo = toolStatus(status?.status);
  const current = status?.current_version || 'nieznany';
  const target = status?.target_version || '—';
  const updateAvailable = Boolean(status?.update_available);

  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true', text: 'UP' }),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'System' }),
        node('h2', { text: 'Auto-update' }),
        node('p', { class: 'muted', text: 'Sprawdzaj nowsze commity, uruchamiaj aktualizację i obserwuj cały proces wdrożenia.' })),
      badge(stateInfo[0], stateInfo[1])),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Commit zainstalowany', current, true),
      toolMeta('Commit kanału', target, true),
      toolMeta('Kanał Git', status?.ref || 'main'),
      toolMeta('Tryb', status?.automatic ? 'Automatyczny' : 'Ręczny')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + (status?.status === 'failed' ? 'bad' : 'ok') }),
        updateAvailable ? 'Nowszy commit jest dostępny' : 'Updater gotowy'),
      button(updateAvailable ? 'Otwórz i zaktualizuj' : 'Otwórz Auto-update', () => navigate('updates'), 'primary'))
  );
}

function unavailableUpdateTool(error) {
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true', text: 'UP' }),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'System' }),
        node('h2', { text: 'Auto-update' }),
        node('p', { class: 'muted', text: 'Sprawdzanie i instalacja nowych commitów Cloudportal.' })),
      badge('Status niedostępny', 'warning')),
    node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się odczytać bieżącego stanu usługi aktualizacji.' }),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' }, node('span', { class: 'status-dot' }), 'Stan serwisu nieznany'),
      button('Otwórz Auto-update', () => navigate('updates'), 'primary'))
  );
}

async function toolsView() {
  let updateCard;
  try {
    const status = await api('/updates/status');
    updateCard = autoUpdateTool(status);
  } catch (error) {
    updateCard = unavailableUpdateTool(error);
  }

  dom.content.replaceChildren(
    heading('Narzędzia administracyjne i serwisowe Cloudportal.'),
    node('section', { class: 'tools-hero' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Centrum narzędzi' }),
        node('h2', { text: 'Narzędzia' }),
        node('p', { class: 'muted', text: 'Operacje systemowe dostępne dla bieżącego użytkownika. Kolejne narzędzia będą pojawiały się w tym miejscu.' })),
      node('div', { class: 'tools-count' },
        node('strong', { text: '1' }),
        node('span', { text: 'narzędzie' }))),
    node('div', { class: 'tools-grid' }, updateCard)
  );
}

registerView({ id: 'tools', label: 'Narzędzia', icon: 'N', permission: 'updates.read', order: 165 }, toolsView);
})();
