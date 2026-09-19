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

function toolStatusDot(status) {
  if (status === 'failed') return 'bad';
  if (['checking', 'running', 'update_available'].includes(status)) return 'warn';
  return 'ok';
}

function toolHealthText(status, updateAvailable) {
  if (status === 'failed') return 'Updater wymaga uwagi';
  if (status === 'checking') return 'Sprawdzanie repozytorium';
  if (status === 'running') return 'Aktualizacja jest w toku';
  if (updateAvailable) return 'Nowszy commit jest dostępny';
  return 'Updater gotowy';
}

function autoUpdateTool(status) {
  const stateInfo = toolStatus(status?.status);
  const current = status?.current_version || 'nieznany';
  const target = status?.target_version || '—';
  const updateAvailable = Boolean(status?.update_available);

  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('refresh')),
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
        node('span', { class: 'status-dot ' + toolStatusDot(status?.status) }),
        toolHealthText(status?.status, updateAvailable)),
      button(updateAvailable ? 'Otwórz i zaktualizuj' : 'Otwórz Auto-update', () => navigate('updates'), 'primary'))
  );
}

function unavailableUpdateTool(error) {
  return node('article', { class: 'panel tool-card' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('refresh')),
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


function hostnameGeneratorTool(schemes = []) {
  const active = schemes.filter(item => item.is_active);
  const next = active[0] || schemes[0] || null;
  return node('article', { class: 'panel tool-card tool-card-featured' },
    node('div', { class: 'tool-card-head' },
      node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('network')),
      node('div', { class: 'tool-title' },
        node('span', { class: 'tool-category', text: 'Automatyzacja VM' }),
        node('h2', { text: 'Generator hostname' }),
        node('p', { class: 'muted', text: 'Twórz wzorce nazw hostów, numeruj je automatycznie i przypisuj patterny do Blueprintów VM.' })),
      badge(active.length ? 'Gotowy' : 'Konfiguracja', active.length ? 'ok' : 'warning')),
    node('div', { class: 'tool-meta-grid' },
      toolMeta('Aktywne patterny', active.length),
      toolMeta('Wszystkie patterny', schemes.length),
      toolMeta('Przykładowy pattern', next?.pattern || '—', true),
      toolMeta('Następny numer', next?.next_number ?? '—')),
    node('div', { class: 'tool-card-footer' },
      node('span', { class: 'tool-health' },
        node('span', { class: 'status-dot ' + (active.length ? 'ok' : 'warn') }),
        active.length ? 'Pattern może być użyty w Blueprint' : 'Utwórz pierwszy pattern hostname'),
      button(active.length ? 'Otwórz generator' : 'Skonfiguruj generator', () => navigate('hostnames'), 'primary'))
  );
}

async function toolsView() {
  const cards = [];

  if (allowed('hostnames.read')) {
    try {
      const schemes = await api('/hostname-schemes?limit=200');
      cards.push(hostnameGeneratorTool(schemes.items || []));
    } catch (error) {
      cards.push(node('article', { class: 'panel tool-card' },
        node('div', { class: 'tool-card-head' },
          node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('network')),
          node('div', { class: 'tool-title' },
            node('span', { class: 'tool-category', text: 'Automatyzacja VM' }),
            node('h2', { text: 'Generator hostname' }),
            node('p', { class: 'muted', text: 'Tworzenie i zarządzanie patternami hostname dla Blueprintów.' })),
          badge('Niedostępny', 'warning')),
        node('p', { class: 'tool-error muted', text: error?.message || 'Nie udało się pobrać patternów hostname.' }),
        node('div', { class: 'tool-card-footer' },
          node('span', { class: 'tool-health' }, node('span', { class: 'status-dot' }), 'Stan generatora nieznany'),
          button('Otwórz generator', () => navigate('hostnames'), 'primary'))));
    }
  }

  if (allowed('updates.read')) {
    try {
      const status = await api('/updates/status');
      cards.push(autoUpdateTool(status));
    } catch (error) {
      cards.push(unavailableUpdateTool(error));
    }
  }

  dom.content.replaceChildren(
    heading('Narzędzia administracyjne i serwisowe Cloudportal.'),
    node('section', { class: 'tools-hero' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Centrum narzędzi' }),
        node('h2', { text: 'Narzędzia' }),
        node('p', { class: 'muted', text: 'Operacje systemowe i automatyzacja infrastruktury dostępne dla bieżącego użytkownika.' })),
      node('div', { class: 'tools-count' },
        node('strong', { text: String(cards.length) }),
        node('span', { text: cards.length === 1 ? 'narzędzie' : 'narzędzia' }))),
    cards.length
      ? node('div', { class: 'tools-grid' }, cards)
      : node('div', { class: 'empty', text: 'Brak narzędzi dostępnych dla bieżących uprawnień.' })
  );
}

registerView({ id: 'tools', label: 'Narzędzia', iconName: 'wrench', order: 155 }, toolsView);
})();
