'use strict';

(() => {
const STATUS_SESSION_KEY = 'cloudportal.update.status_token';
const UPDATE_PHASES = [
  ['preflight', 'Przygotowanie'],
  ['backup', 'Backup'],
  ['packages', 'Pakiety'],
  ['download', 'Pobieranie'],
  ['python', 'Python'],
  ['updater', 'Updater'],
  ['database', 'Baza danych'],
  ['systemd', 'Usługi'],
  ['tls', 'TLS'],
  ['proxy', 'Reverse proxy'],
  ['services', 'Restart'],
  ['healthcheck', 'Healthcheck'],
  ['bootstrap', 'Finalizacja'],
  ['complete', 'Gotowe'],
];
let updatePollTimer = null;

function normalizedUpdateStatus(status) {
  if (!status || typeof status !== 'object') return status;
  if (status.status === 'running' && status.phase === 'complete' && Number(status.progress || 0) >= 100) {
    return {
      ...status,
      status: 'success',
      progress: 100,
      update_available: false,
      message: status.message || 'Aktualizacja zakończona pomyślnie.',
    };
  }
  return status;
}

function updateStatusKind(status) {
  if (status === 'success' || status === 'up_to_date' || status === 'local_ahead') return 'ok';
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
    up_to_date: 'Aktualny commit',
    local_ahead: 'Zainstalowany commit nowszy',
  };
  return labels[status] || status || '—';
}

function phaseLabel(phase) {
  const special = {
    idle: 'Oczekiwanie',
    checking: 'Sprawdzanie repozytorium',
    available: 'Aktualizacja dostępna',
    up_to_date: 'Wersja aktualna',
    check_failed: 'Błąd sprawdzania',
    install: 'Uruchamianie instalatora',
    failed: 'Błąd aktualizacji',
    interrupted: 'Przerwany proces',
    local_ahead: 'Zainstalowany commit nowszy',
  };
  return new Map(UPDATE_PHASES).get(phase) || special[phase] || phase || 'Oczekiwanie';
}

function updateTone(status) {
  if (status === 'failed') return 'danger';
  if (status === 'running' || status === 'checking') return 'warning';
  if (status === 'update_available') return 'info';
  if (status === 'success' || status === 'up_to_date' || status === 'local_ahead') return 'ok';
  return 'neutral';
}

function updateBusy(status) {
  return ['running', 'checking'].includes(status);
}

function formatDuration(start, end) {
  if (!start) return '—';
  const from = new Date(start);
  const to = end ? new Date(end) : new Date();
  if (Number.isNaN(from.valueOf()) || Number.isNaN(to.valueOf())) return '—';
  const seconds = Math.max(0, Math.round((to - from) / 1000));
  if (seconds < 60) return seconds + ' s';
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  if (minutes < 60) return rest ? minutes + ' min ' + rest + ' s' : minutes + ' min';
  const hours = Math.floor(minutes / 60);
  return hours + ' h ' + (minutes % 60) + ' min';
}

function nextCheckText(status, settings) {
  if (!settings.enabled) return 'Auto-update wyłączony';
  if (!status.last_check_at) return 'Po uruchomieniu harmonogramu';
  const last = new Date(status.last_check_at);
  if (Number.isNaN(last.valueOf())) return '—';
  const next = new Date(last.getTime() + Number(settings.interval_hours || 24) * 3600000);
  return formatDate(next.toISOString());
}

function versionValue(value) {
  return value || 'nieznana';
}

function commitRelationLabel(status) {
  const labels = {
    identical: 'Ten sam commit',
    target_newer: 'Kanał ma nowszy commit',
    target_newer_diverged: 'Kanał ma nowszy commit na rozbieżnej historii',
    current_newer: 'Zainstalowany commit jest nowszy',
    current_newer_diverged: 'Zainstalowany commit jest nowszy na rozbieżnej historii',
    unknown_current: 'Nieznany commit lokalny',
  };
  return labels[status.commit_relation] || 'Nieustalona';
}

function commitDifference(status) {
  const ahead = Number(status.ahead_by || 0);
  const behind = Number(status.behind_by || 0);
  if (status.commit_relation === 'target_newer') return '+' + ahead + ' commitów w kanale';
  if (status.commit_relation === 'current_newer') return '+' + behind + ' commitów lokalnie';
  if (status.commit_relation === 'identical') return '0 commitów';
  if (status.commit_relation && status.commit_relation.includes('diverged')) {
    return 'Historie rozbieżne';
  }
  return '—';
}

function statusDescription(status) {
  if (status.status === 'running') return 'Nowszy commit jest wdrażany. Panel statusu działa niezależnie od głównego API.';
  if (status.status === 'checking') return 'Updater porównuje zainstalowany commit z HEAD wybranego kanału Git.';
  if (status.status === 'update_available') {
    const count = Number(status.ahead_by || 0);
    return count > 0
      ? 'Kanał ma ' + count + ' nowszych commitów. Najnowszy commit jest gotowy do instalacji.'
      : 'Kanał wskazuje nowszy commit gotowy do instalacji.';
  }
  if (status.status === 'up_to_date') return 'Zainstalowany SHA jest identyczny z HEAD wybranego kanału.';
  if (status.status === 'local_ahead') return 'Zainstalowany commit jest nowszy niż commit kanału. Updater nie wykona downgrade’u.';
  if (status.status === 'success') return 'Ostatnia aktualizacja zakończyła się instalacją nowszego commita.';
  if (status.status === 'failed') return 'Proces aktualizacji został zatrzymany. Szczegóły znajdują się w logu technicznym.';
  return 'Wersja Cloudportal jest identyfikowana przez commit Git.';
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

function versionCard(label, value, description, emphasis) {
  return node('div', { class: 'update-version-card ' + (emphasis || '') },
    node('span', { class: 'update-card-label', text: label }),
    node('strong', { class: 'mono update-version-value', text: versionValue(value) }),
    node('small', { class: 'muted', text: description })
  );
}

function updatePipeline(status) {
  const currentIndex = UPDATE_PHASES.findIndex(item => item[0] === status.phase);
  const complete = status.status === 'success';
  const failed = status.status === 'failed';
  const running = status.status === 'running' && currentIndex >= 0;
  const visibleStep = complete
    ? UPDATE_PHASES.length
    : currentIndex >= 0 ? currentIndex + 1 : 0;

  const pipelineCaption = complete
    ? 'Wszystkie ' + UPDATE_PHASES.length + ' etapów zakończone'
    : failed && currentIndex >= 0
      ? 'Zatrzymano na etapie: ' + phaseLabel(status.phase)
      : failed
        ? 'Błąd przed rozpoczęciem instalacji'
        : running
          ? 'Wykonywany etap: ' + phaseLabel(status.phase)
          : status.status === 'update_available'
            ? 'Aktualizacja gotowa — etapy rozpoczną się po uruchomieniu instalacji'
            : status.status === 'checking'
              ? 'Sprawdzanie dostępności aktualizacji'
              : status.status === 'running'
                ? 'Uruchamianie instalatora — pierwszy etap jeszcze się nie rozpoczął'
                : 'Proces instalacji nie został uruchomiony';

  return node('div', { class: 'update-pipeline-shell' },
    node('div', { class: 'update-pipeline-meta' },
      node('div', { class: 'update-pipeline-caption-wrap' },
        node('span', { class: 'update-pipeline-caption', text: pipelineCaption }),
        node('small', { class: 'muted', text: visibleStep
          ? 'Postęp etapów instalacji'
          : 'Etapy pozostają w kolejce do czasu rozpoczęcia aktualizacji' })),
      node('span', { class: 'update-pipeline-count mono', text: visibleStep + '/' + UPDATE_PHASES.length })
    ),
    node('div', { class: 'update-pipeline', role: 'list', 'aria-label': 'Etapy aktualizacji' },
      ...UPDATE_PHASES.map((item, index) => {
        const label = item[1];
        let stateClass = 'pending';
        let marker = String(index + 1);
        let stateText = 'Oczekuje';

        if (complete || index < currentIndex) {
          stateClass = 'complete';
          marker = '✓';
          stateText = 'Gotowe';
        } else if (index === currentIndex) {
          stateClass = failed ? 'failed' : 'active';
          marker = failed ? '!' : String(index + 1);
          stateText = failed ? 'Błąd' : 'W toku';
        }

        const attrs = {
          class: 'update-pipeline-step ' + stateClass,
          role: 'listitem',
        };
        if (index === currentIndex && !complete) attrs['aria-current'] = 'step';

        return node('div', attrs,
          node('div', { class: 'update-pipeline-marker', 'aria-hidden': 'true', text: marker }),
          node('div', { class: 'update-pipeline-step-copy' },
            node('span', { class: 'update-pipeline-label', text: label }),
            node('small', { class: 'update-pipeline-step-state', text: stateText }))
        );
      })
    )
  );
}

function updateFacts(status, settings) {
  return node('div', { class: 'update-facts' },
    info('Model wersji', 'Git commit'),
    info('Relacja commitów', commitRelationLabel(status)),
    info('Różnica', commitDifference(status)),
    info('Tryb uruchomienia', status.automatic ? 'Automatyczny' : 'Ręczny'),
    info('Commit lokalny', formatDate(status.current_commit_at)),
    info('Commit kanału', formatDate(status.target_commit_at)),
    info('Ostatnie sprawdzenie', formatDate(status.last_check_at)),
    info('Rozpoczęto', formatDate(status.started_at)),
    info('Zakończono', formatDate(status.finished_at)),
    info('Czas operacji', formatDuration(status.started_at, status.finished_at)),
    info('Następne sprawdzenie', nextCheckText(status, settings))
  );
}

function updateEventTimeline(status) {
  const events = (status.events || []).slice(-24).reverse();
  if (!events.length) {
    return node('div', { class: 'update-empty-state' },
      node('strong', { text: 'Brak historii operacji' }),
      node('span', { class: 'muted', text: 'Po sprawdzeniu lub uruchomieniu aktualizacji pojawią się tutaj kolejne etapy.' })
    );
  }
  return node('div', { class: 'update-events' }, ...events.map((item, index) =>
    node('div', { class: 'update-event ' + (index === 0 ? 'latest' : '') },
      node('div', { class: 'update-event-rail' },
        node('span', { class: 'update-event-dot' }),
        node('span', { class: 'update-event-line' })),
      node('div', { class: 'update-event-copy' },
        node('div', { class: 'update-event-head' },
          node('strong', { text: item.message }),
          node('span', { class: 'mono muted', text: String(item.progress) + '%' })),
        node('small', { class: 'muted', text: phaseLabel(item.phase) + ' · ' + formatDate(item.at) })
      )
    )
  ));
}

function technicalLog(status) {
  const output = (status.output || []).slice(-120).join('\n');
  const expanded = ['running', 'failed'].includes(status.status);
  return node('details', { class: 'panel update-log-panel', open: expanded },
    node('summary', {},
      node('div', {},
        node('strong', { text: 'Log techniczny' }),
        node('small', { class: 'muted', text: output ? Math.min((status.output || []).length, 120) + ' ostatnich wpisów' : 'Brak wpisów' })),
      node('span', { class: 'update-log-chevron', 'aria-hidden': 'true', text: '⌄' })),
    node('div', { class: 'update-log-toolbar' },
      node('span', { class: 'muted', text: 'Sekrety i tokeny są maskowane przez serwis aktualizacji.' }),
      output ? button('Kopiuj log', async () => {
        try {
          await copyText(output);
          toast('Log skopiowany.');
        } catch (error) {
          toast(error.message, 'error');
        }
      }) : null),
    node('pre', { class: 'log-output mono update-log', text: output || 'Brak logów dla bieżącej sesji aktualizacji.' })
  );
}

function statusActions(status, container, settings) {
  const busy = updateBusy(status.status);
  const group = node('div', { class: 'update-hero-actions' });
  const checkButton = node('button', {
    class: 'button ghost',
    type: 'button',
    disabled: busy,
    onClick: async () => {
      checkButton.disabled = true;
      try {
        await api('/updates/check', { method: 'POST', body: {}, idempotent: true });
        await renderLiveStatus(container, settings);
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        checkButton.disabled = false;
      }
    },
  }, status.status === 'checking' ? 'Sprawdzanie…' : 'Sprawdź aktualizacje');
  group.append(checkButton);

  if (allowed('updates.execute')) {
    const noNewerCommit = ['up_to_date', 'local_ahead'].includes(status.status);
    const installLabel = status.status === 'update_available'
      ? 'Zainstaluj nowszy commit'
      : noNewerCommit ? 'Brak nowszego commita' : 'Aktualizuj teraz';
    group.append(node('button', {
      class: 'button primary',
      type: 'button',
      disabled: busy || noNewerCommit,
      onClick: () => {
        confirmAction(
          'Aktualizacja Cloudportal',
          'Zostanie utworzony backup PostgreSQL, pobrana nowa wersja i zrestartowane usługi aplikacji. Serwis aktualizacji i ten podgląd pozostaną aktywne.',
          async () => {
            await api('/updates/run', { method: 'POST', body: {}, idempotent: true });
            toast('Aktualizacja uruchomiona.');
            await renderLiveStatus(container, settings);
          }
        );
      },
    }, busy ? 'Aktualizacja trwa…' : installLabel));
  }
  return group;
}

function statusPanel(status, settings, container) {
  status = normalizedUpdateStatus(status);
  const rawProgress = Math.max(0, Math.min(100, Number(status.progress || 0)));
  const installPhaseKnown = UPDATE_PHASES.some(item => item[0] === status.phase);
  const installProgressVisible = status.status === 'success'
    || status.status === 'failed' && installPhaseKnown
    || status.status === 'running' && installPhaseKnown;
  const progress = installProgressVisible ? rawProgress : 0;
  const fill = node('div', { class: 'update-progress-fill' + (status.status === 'running' && installPhaseKnown ? ' is-running' : '') });
  fill.style.width = progress + '%';
  const progressStateText = status.status === 'success'
    ? 'Zakończono'
    : status.status === 'failed'
      ? 'Przerwano'
      : status.status === 'running' && installPhaseKnown
        ? 'W toku'
        : status.status === 'update_available'
          ? 'Gotowa do instalacji'
          : status.status === 'checking'
            ? 'Sprawdzanie'
            : status.status === 'running'
              ? 'Uruchamianie'
              : 'Nie rozpoczęto';
  const tone = updateTone(status.status);
  const symbol = status.status === 'failed'
    ? '!'
    : status.status === 'update_available'
      ? '↓'
      : status.status === 'local_ahead'
        ? '↑'
        : ['running', 'checking'].includes(status.status)
          ? '↻'
          : ['success', 'up_to_date'].includes(status.status) ? '✓' : 'G';

  return node('div', { class: 'stack update-status-stack' },
    node('section', { class: 'update-hero update-hero-' + tone },
      node('div', { class: 'update-hero-main' },
        node('div', { class: 'update-status-orb', 'aria-hidden': 'true' }, node('span', { text: symbol })),
        node('div', { class: 'update-hero-copy' },
          node('div', { class: 'update-hero-kicker' },
            badge(updateStatusLabel(status.status), updateStatusKind(status.status)),
            node('span', { class: 'update-live-indicator' }, node('span', { class: 'update-live-dot' }), 'Updater online')),
          node('h2', { text: status.message || 'Centrum aktualizacji' }),
          node('p', { text: statusDescription(status) })
        )
      ),
      statusActions(status, container, settings)
    ),

    node('div', { class: 'update-version-grid' },
      versionCard('Commit zainstalowany', status.current_version, status.current_commit_at ? 'Commit: ' + formatDate(status.current_commit_at) : 'Aktualnie uruchomiony release'),
      node('div', { class: 'update-version-arrow', 'aria-hidden': 'true', text: '→' }),
      versionCard('Commit kanału', status.target_version, status.target_commit_at ? 'HEAD: ' + formatDate(status.target_commit_at) : (status.update_available ? 'Gotowy do instalacji' : 'Ostatnio wykryty commit'), status.update_available ? 'available' : ''),
      versionCard('Kanał Git', status.ref || settings.ref, 'Branch, tag lub commit obserwowany przez updater')
    ),

    node('section', { class: 'panel update-progress-panel' },
      node('div', { class: 'update-section-heading' },
        node('div', {},
          node('span', { class: 'update-card-label', text: 'Postęp operacji' }),
          node('h2', { text: phaseLabel(status.phase) })),
        node('div', { class: 'update-progress-metric' },
          node('span', { class: 'update-progress-metric-label', text: installProgressVisible ? 'Postęp' : 'Stan procesu' }),
          installProgressVisible
            ? node('div', { class: 'update-progress-number' },
                node('strong', { text: String(progress) }),
                node('span', { text: '%' }))
            : node('span', { class: 'update-progress-status', text: progressStateText }))
      ),
      node('div', {
        class: 'update-progress-track' + (installProgressVisible ? '' : ' is-idle'),
        role: 'progressbar',
        'aria-label': installProgressVisible ? 'Postęp aktualizacji' : 'Postęp instalacji — proces nieuruchomiony',
        'aria-valuemin': '0',
        'aria-valuemax': '100',
        'aria-valuenow': String(progress),
      }, fill),
      updatePipeline(status)
    ),

    node('div', { class: 'update-detail-grid' },
      node('section', { class: 'panel update-history-panel' },
        node('div', { class: 'update-section-heading' },
          node('div', {}, node('span', { class: 'update-card-label', text: 'Historia' }), node('h2', { text: 'Przebieg aktualizacji' })),
          badge((status.events || []).length + ' zdarzeń', 'info')),
        updateEventTimeline(status)
      ),
      node('section', { class: 'panel update-facts-panel' },
        node('div', { class: 'update-section-heading' },
          node('div', {}, node('span', { class: 'update-card-label', text: 'Szczegóły' }), node('h2', { text: 'Informacje o procesie' }))),
        updateFacts(status, settings),
        node('div', { class: 'update-service-note' },
          node('strong', { text: 'Niezależny serwis systemd' }),
          node('span', { class: 'muted', text: 'Podgląd pozostaje dostępny podczas restartu API, dispatchera i workerów.' }))
      )
    ),

    technicalLog(status)
  );
}

async function renderLiveStatus(container, settings) {
  try {
    const status = normalizedUpdateStatus(await resilientUpdateStatus());
    container.replaceChildren(statusPanel(status, settings, container));
  } catch (error) {
    container.replaceChildren(node('section', { class: 'panel update-unavailable' },
      node('div', { class: 'update-status-orb', 'aria-hidden': 'true' }, node('span', { text: '!' })),
      node('div', {},
        node('h2', { text: 'Serwis aktualizacji jest niedostępny' }),
        node('p', { class: 'form-error', text: error.message }),
        node('p', { class: 'muted', text: 'Główna aplikacja nadal może działać. Sprawdź usługę cloudportal-updater.service.' }))
    ));
  }
}

function scheduleStatusPoll(container, settings) {
  if (updatePollTimer) clearTimeout(updatePollTimer);
  if (state.view !== 'updates') return;
  updatePollTimer = window.setTimeout(async () => {
    await renderLiveStatus(container, settings);
    scheduleStatusPoll(container, settings);
  }, 1500);
}

function intervalPresets(intervalInput) {
  return node('div', { class: 'update-interval-presets' },
    ...[[6, '6 h'], [12, '12 h'], [24, '24 h'], [72, '3 dni'], [168, '7 dni']].map(item =>
      node('button', {
        type: 'button',
        class: 'update-preset ' + (Number(intervalInput.value) === item[0] ? 'active' : ''),
        'data-value': String(item[0]),
        disabled: intervalInput.disabled,
        onClick: event => {
          intervalInput.value = String(item[0]);
          event.currentTarget.parentElement.querySelectorAll('.update-preset').forEach(preset => {
            preset.classList.toggle('active', preset === event.currentTarget);
          });
          intervalInput.dispatchEvent(new Event('input', { bubbles: true }));
        },
      }, item[1])
    )
  );
}

function settingsPanel(settings, statusRoot) {
  const canUpdateSettings = allowed('updates.update');
  const autoEnabled = node('input', { type: 'checkbox', name: 'enabled', checked: settings.enabled, disabled: !canUpdateSettings });
  const interval = node('input', { type: 'number', name: 'interval_hours', min: '1', max: '168', value: settings.interval_hours, disabled: !canUpdateSettings });
  const ref = node('input', { type: 'text', name: 'ref', value: settings.ref, maxlength: '200', class: 'mono', disabled: !canUpdateSettings });
  const save = node('button', { class: 'button primary', type: 'submit', disabled: true }, 'Zapisz zmiany');
  const dirty = node('span', { class: 'update-unsaved', hidden: true, text: 'Niezapisane zmiany' });
  const stateBadge = badge(settings.enabled ? 'Auto-update włączony' : 'Auto-update wyłączony', settings.enabled ? 'ok' : '');
  const channelPreviewValue = node('strong', { class: 'mono', text: settings.ref });
  const presets = intervalPresets(interval);

  const syncPreset = () => {
    presets.querySelectorAll('.update-preset').forEach(item => {
      item.classList.toggle('active', Number(item.dataset.value) === Number(interval.value));
    });
  };

  const markDirty = () => {
    const changed = autoEnabled.checked !== Boolean(settings.enabled)
      || Number(interval.value) !== Number(settings.interval_hours)
      || ref.value.trim() !== settings.ref;
    dirty.hidden = !changed;
    save.disabled = !canUpdateSettings || !changed;
  };
  autoEnabled.addEventListener('change', markDirty);
  interval.addEventListener('input', () => {
    syncPreset();
    markDirty();
  });
  ref.addEventListener('input', markDirty);

  return node('form', {
    class: 'panel update-settings',
    onSubmit: async event => {
      event.preventDefault();
      save.disabled = true;
      try {
        const saved = await api('/updates/settings', {
          method: 'PUT',
          body: {
            enabled: autoEnabled.checked,
            interval_hours: Number(interval.value),
            ref: ref.value.trim(),
          },
        });
        Object.assign(settings, saved);
        autoEnabled.checked = saved.enabled;
        interval.value = saved.interval_hours;
        ref.value = saved.ref;
        stateBadge.textContent = saved.enabled ? 'Auto-update włączony' : 'Auto-update wyłączony';
        stateBadge.className = 'badge ' + (saved.enabled ? 'ok' : '');
        channelPreviewValue.textContent = saved.ref;
        syncPreset();
        dirty.hidden = true;
        toast('Ustawienia aktualizacji zapisane.');
        await renderLiveStatus(statusRoot, settings);
      } catch (error) {
        toast(error.message, 'error');
      } finally {
        markDirty();
      }
    },
  },
    node('div', { class: 'update-settings-header' },
      node('div', {},
        node('span', { class: 'update-card-label', text: 'Automatyzacja' }),
        node('h2', { text: 'Polityka auto-update' }),
        node('p', { class: 'muted', text: 'Updater okresowo sprawdza repozytorium. Po wykryciu nowej wersji może sam wykonać backup i wdrożenie.' })),
      node('div', { class: 'update-settings-state' },
        stateBadge,
        dirty)
    ),
    node('label', { class: 'update-master-toggle' },
      node('div', { class: 'update-toggle-control' }, autoEnabled, node('span', { class: 'update-toggle-track' })),
      node('div', {},
        node('strong', { text: 'Automatycznie instaluj nowe wersje' }),
        node('span', { class: 'muted', text: 'Po wykryciu nowego commita aktualizacja rozpocznie się bez ręcznego zatwierdzania.' }))
    ),
    node('div', { class: 'update-settings-grid' },
      node('div', { class: 'update-setting-block' },
        node('label', {}, node('span', { text: 'Interwał sprawdzania' }), interval),
        presets,
        node('small', { class: 'muted', text: 'Zakres: od 1 do 168 godzin.' })
      ),
      node('div', { class: 'update-setting-block' },
        node('label', {}, node('span', { text: 'Kanał / Git ref' }), ref),
        node('small', { class: 'muted', text: 'Branch, tag lub commit używany jako źródło aktualizacji.' }),
        node('div', { class: 'update-channel-preview' },
          node('span', { class: 'muted', text: 'Aktywny kanał' }),
          channelPreviewValue)
      )
    ),
    node('div', { class: 'update-settings-footer' },
      canUpdateSettings
        ? node('span', { class: 'muted', text: 'Zmiana kanału aktualizacji wymaga uprawnienia updates.update.' })
        : node('span', { class: 'muted', text: 'Tryb tylko do odczytu. Brak uprawnienia updates.update.' }),
      canUpdateSettings ? save : null
    )
  );
}

async function updatesView() {
  const results = await Promise.all([
    api('/updates/settings'),
    resilientUpdateStatus(),
  ]);
  const settings = { ...results[0] };
  const initialStatus = normalizedUpdateStatus(results[1]);
  const statusRoot = node('div', { class: 'stack update-live-root' });
  statusRoot.replaceChildren(statusPanel(initialStatus, settings, statusRoot));

  dom.content.replaceChildren(
    heading(
      'Bezpieczne aktualizacje Cloudportal z ciągłym podglądem procesu, backupem bazy i niezależnym serwisem wykonawczym.',
      [button('← Narzędzia', () => navigate('tools'))]
    ),
    statusRoot,
    settingsPanel(settings, statusRoot),
    node('section', { class: 'update-safety-grid' },
      node('div', { class: 'panel update-safety-card' },
        node('span', { class: 'update-safety-icon', 'aria-hidden': 'true' }, appIcon('database')),
        node('div', {}, node('strong', { text: 'Backup przed wdrożeniem' }), node('span', { class: 'muted', text: 'Przed zmianą release wykonywany jest backup PostgreSQL.' }))),
      node('div', { class: 'panel update-safety-card' },
        node('span', { class: 'update-safety-icon', 'aria-hidden': 'true' }, appIcon('server')),
        node('div', {}, node('strong', { text: 'Podgląd niezależny od API' }), node('span', { class: 'muted', text: 'Status aktualizacji jest serwowany przez osobny proces na loopback.' }))),
      node('div', { class: 'panel update-safety-card' },
        node('span', { class: 'update-safety-icon', 'aria-hidden': 'true' }, appIcon('shield')),
        node('div', {}, node('strong', { text: 'Kontrola uprawnień' }), node('span', { class: 'muted', text: 'Odczyt, wykonanie i zmiana kanału mają oddzielne uprawnienia.' })))
    )
  );

  scheduleStatusPoll(statusRoot, settings);
}

document.addEventListener('cloudportal:app-hidden', () => {
  if (updatePollTimer) window.clearTimeout(updatePollTimer);
  updatePollTimer = null;
  try { sessionStorage.removeItem(STATUS_SESSION_KEY); } catch { /* ignore */ }
});

registerView({
  id: 'updates',
  label: 'Auto-update',
  iconName: 'refresh',
  permission: 'updates.read',
  order: 166,
  navigation: false,
  navigationParent: 'tools',
}, updatesView);
})();
