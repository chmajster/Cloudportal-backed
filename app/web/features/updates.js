'use strict';

(() => {
const STATUS_SESSION_KEY = 'cloudportal.update.status_token';
let updatePollTimer = null;

function updateStatusKind(status) {
  if (status === 'success' || status === 'up_to_date') return 'ok';
  if (status === 'failed') return 'danger';
  if (status === 'running' || status === 'checking') return 'warning';
  if (status === 'update_available') return 'info';
  return '';
}

function updateStatusLabel(status) {
  const labels = {
    idle: 'Gotowy',
    checking: 'Sprawdzanie',
    update_available: 'Dostępna aktualizacja',
    running: 'Aktualizacja trwa',
    success: 'Zakończono',
    failed: 'Błąd',
    up_to_date: 'Aktualny',
  };
  return labels[status] || status || '—';
}

async function updateStatusToken() {
  let token = '';
  try { token = sessionStorage.getItem(STATUS_SESSION_KEY) || ''; } catch { /* ignore */ }
  if (token) return token;
  const result = await api('/updates/status-access');
  token = result.token || '';
  if (!token) throw new Error('Brak tokenu podglądu aktualizacji.');
  try { sessionStorage.setItem(STATUS_SESSION_KEY, token); } catch { /* ignore */ }
  return token;
}

async function resilientUpdateStatus() {
  const token = await updateStatusToken();
  const response = await fetch('/update-status', { cache: 'no-store', headers: { 'X-Update-Status-Token': token } });
  if (response.status === 401) {
    try { sessionStorage.removeItem(STATUS_SESSION_KEY); } catch { /* ignore */ }
    const refreshed = await updateStatusToken();
    const retry = await fetch('/update-status', { cache: 'no-store', headers: { 'X-Update-Status-Token': refreshed } });
    if (!retry.ok) throw new Error('Serwis aktualizacji jest niedostępny.');
    return retry.json();
  }
  if (!response.ok) throw new Error('Serwis aktualizacji jest niedostępny.');
  return response.json();
}

function statusPanel(status) {
  const fill = node('div', { class: 'update-progress-fill' });
  fill.style.width = Math.max(0, Math.min(100, Number(status.progress || 0))) + '%';
  const events = (status.events || []).slice().reverse();
  const output = (status.output || []).slice(-80).join('\n');
  return node('div', { class: 'stack update-status-stack' },
    node('div', { class: 'update-summary' },
      node('div', {}, node('span', { class: 'muted', text: 'Stan' }), node('div', {}, badge(updateStatusLabel(status.status), updateStatusKind(status.status)))),
      node('div', {}, node('span', { class: 'muted', text: 'Zainstalowana' }), node('strong', { class: 'mono', text: status.current_version || 'nieznana' })),
      node('div', {}, node('span', { class: 'muted', text: 'Docelowa' }), node('strong', { class: 'mono', text: status.target_version || '—' })),
      node('div', {}, node('span', { class: 'muted', text: 'Ref' }), node('strong', { class: 'mono', text: status.ref || '—' }))
    ),
    node('section', { class: 'panel update-progress-panel' },
      node('div', { class: 'panel-header' },
        node('div', {}, node('h2', { text: status.message || 'Aktualizacja' }), node('p', { class: 'muted', text: 'Etap: ' + (status.phase || '—') })),
        node('strong', { class: 'update-progress-value', text: String(status.progress || 0) + '%' })
      ),
      node('div', { class: 'update-progress-track', role: 'progressbar', 'aria-valuemin': '0', 'aria-valuemax': '100', 'aria-valuenow': String(status.progress || 0) }, fill)
    ),
    node('div', { class: 'update-detail-grid' },
      node('section', { class: 'panel' },
        node('div', { class: 'panel-header' }, node('h2', { text: 'Przebieg' })),
        events.length ? node('div', { class: 'update-events' }, events.map(item =>
          node('div', { class: 'update-event' },
            node('span', { class: 'mono muted', text: String(item.progress) + '%' }),
            node('div', {}, node('strong', { text: item.message }), node('small', { class: 'muted', text: item.phase + ' · ' + formatDate(item.at) }))
          )
        )) : node('div', { class: 'empty', text: 'Brak zdarzeń aktualizacji.' })
      ),
      node('section', { class: 'panel' },
        node('div', { class: 'panel-header' }, node('h2', { text: 'Log serwisu aktualizacji' }), badge('live', 'info')),
        node('pre', { class: 'log-output mono update-log', text: output || 'Brak logów.' })
      )
    )
  );
}

async function renderLiveStatus(container) {
  try {
    const status = await resilientUpdateStatus();
    container.replaceChildren(statusPanel(status));
  } catch (error) {
    container.replaceChildren(node('div', { class: 'panel' },
      node('h2', { text: 'Nie można odczytać stanu aktualizacji' }),
      node('p', { class: 'form-error', text: error.message })
    ));
  }
}

function scheduleStatusPoll(container) {
  if (updatePollTimer) clearTimeout(updatePollTimer);
  if (state.view !== 'updates') return;
  updatePollTimer = window.setTimeout(async () => {
    await renderLiveStatus(container);
    scheduleStatusPoll(container);
  }, 1500);
}

async function updatesView() {
  const [settings, initialStatus] = await Promise.all([
    api('/updates/settings'),
    resilientUpdateStatus(),
  ]);

  const statusRoot = node('div', { class: 'stack' });
  statusRoot.replaceChildren(statusPanel(initialStatus));

  const canUpdateSettings = allowed('updates.update');
  const autoEnabled = node('input', { type: 'checkbox', name: 'enabled', checked: settings.enabled, disabled: !canUpdateSettings });
  const interval = node('input', { type: 'number', name: 'interval_hours', min: '1', max: '168', value: settings.interval_hours, class: 'input', disabled: !canUpdateSettings });
  const ref = node('input', { type: 'text', name: 'ref', value: settings.ref, maxlength: '200', class: 'input mono', disabled: !canUpdateSettings });
  const settingsForm = node('form', {
    class: 'panel stack update-settings',
    onSubmit: async event => {
      event.preventDefault();
      const submit = event.currentTarget.querySelector('button[type="submit"]');
      if (submit) submit.disabled = true;
      try {
        const saved = await api('/updates/settings', {
          method: 'PUT',
          body: {
            enabled: autoEnabled.checked,
            interval_hours: Number(interval.value),
            ref: ref.value.trim(),
          },
        });
        autoEnabled.checked = saved.enabled;
        interval.value = saved.interval_hours;
        ref.value = saved.ref;
        toast('Ustawienia aktualizacji zapisane.');
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        if (submit) submit.disabled = false;
      }
    },
  },
    node('div', { class: 'panel-header' }, node('div', {}, node('h2', { text: 'Auto-update' }), node('p', { class: 'muted', text: 'Oddzielny serwis sprawdza GitHub i wykonuje aktualizację bez utraty podglądu postępu.' }))),
    node('label', { class: 'switch-row' }, autoEnabled, node('span', {}, node('strong', { text: 'Automatycznie instaluj aktualizacje' }), node('small', { class: 'muted', text: 'Domyślnie wyłączone. Aktualizacja restartuje API, dispatcher i workery.' }))),
    node('div', { class: 'form-grid' },
      node('label', { class: 'field' }, node('span', { text: 'Interwał sprawdzania [h]' }), interval),
      node('label', { class: 'field' }, node('span', { text: 'Git ref / kanał' }), ref)
    ),
    canUpdateSettings ? node('div', { class: 'actions' }, node('button', { class: 'button primary', type: 'submit' }, 'Zapisz ustawienia')) : null
  );

  const actions = node('div', { class: 'actions' });
  actions.append(button('Sprawdź aktualizacje', async () => {
    try {
      await api('/updates/check', { method: 'POST', body: {}, idempotent: true });
      await renderLiveStatus(statusRoot);
    } catch (error) { toast(error.message, 'error'); }
  }));
  if (allowed('updates.execute')) {
    actions.append(button('Aktualizuj teraz', () => {
      confirmAction(
        'Aktualizacja Cloudportal',
        'Zostanie utworzony backup PostgreSQL, pobrana nowa wersja i zrestartowane usługi aplikacji. Serwis aktualizacji oraz ten podgląd pozostaną aktywne.',
        async () => {
          await api('/updates/run', { method: 'POST', body: {}, idempotent: true });
          toast('Aktualizacja uruchomiona.');
          await renderLiveStatus(statusRoot);
        }
      );
    }, 'primary'));
  }

  dom.content.replaceChildren(
    heading('Aktualizacja aplikacji jest wykonywana przez niezależny serwis systemd. Postęp pozostaje widoczny także podczas restartu głównego API.', [actions]),
    settingsForm,
    statusRoot
  );
  scheduleStatusPoll(statusRoot);
}

registerView({ id: 'updates', label: 'Aktualizacje', icon: 'U', permission: 'updates.read', order: 170 }, updatesView);
})();
