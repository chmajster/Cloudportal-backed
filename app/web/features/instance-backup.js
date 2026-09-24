'use strict';

(() => {
const BACKUP_STAGE_ORDER = [
  'queued', 'preparing', 'database_dump', 'configuration',
  'secret_material', 'manifest', 'checksums', 'archive', 'ready',
];
const BACKUP_STAGES = [
  ['preparing', 'Sprawdzono środowisko'],
  ['preparing', 'Odczytano metadane aplikacji'],
  ['database_dump', 'Tworzenie dumpa PostgreSQL'],
  ['configuration', 'Zebranie konfiguracji'],
  ['secret_material', 'Zebranie wymaganych kluczy'],
  ['manifest', 'Generowanie manifestu'],
  ['checksums', 'Obliczanie checksum'],
  ['archive', 'Budowanie archiwum'],
  ['ready', 'Backup gotowy'],
];

function delay(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function formatBytes(value) {
  const bytes = Number(value || 0);
  if (!bytes) return '—';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = bytes;
  let index = 0;
  while (size >= 1024 && index < units.length - 1) {
    size /= 1024;
    index += 1;
  }
  return (index ? size.toFixed(size >= 10 ? 1 : 2) : String(size)) + ' ' + units[index];
}

function formatDate(value) {
  if (!value) return '—';
  const date = new Date(value.endsWith?.('Z') ? value : value + 'Z');
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('pl-PL');
}

function metaItem(label, value, mono = false) {
  return node('div', { class: 'instance-backup-meta-item' },
    node('span', { class: 'muted', text: label }),
    node('strong', { class: mono ? 'mono' : '', text: value || '—' }));
}

function stageList(currentCode, failed = false) {
  const currentRank = BACKUP_STAGE_ORDER.indexOf(currentCode);
  return node('div', { class: 'instance-backup-stages' },
    ...BACKUP_STAGES.map(([code, label]) => {
      const stageRank = BACKUP_STAGE_ORDER.indexOf(code);
      let marker = '[    ]';
      let stateClass = 'pending';
      if (!failed && currentCode === 'ready') {
        marker = '[ OK ]';
        stateClass = 'done';
      } else if (stageRank >= 0 && currentRank > stageRank) {
        marker = '[ OK ]';
        stateClass = 'done';
      } else if (stageRank >= 0 && currentRank === stageRank && !failed) {
        marker = '[ .. ]';
        stateClass = 'active';
      }
      return node('div', { class: 'instance-backup-stage ' + stateClass },
        node('span', { class: 'mono', text: marker }),
        node('span', { text: label }));
    }));
}

async function authenticatedFetch(path, options = {}, canRefresh = true) {
  const headers = {
    'X-Request-ID': crypto.randomUUID(),
    'X-Portal-Source': 'Cloudportal-backed',
    ...(options.headers || {}),
  };
  if (state.session?.access_token) headers.Authorization = 'Bearer ' + state.session.access_token;
  const response = await fetch(API + path, { ...options, headers });
  if (response.status === 401 && canRefresh && state.session?.refresh_token) {
    await window.cloudportalHttp.refreshSession();
    return authenticatedFetch(path, options, false);
  }
  return response;
}

async function responseError(response) {
  const data = await response.json().catch(() => ({}));
  throw new ApiError(response.status, data);
}

async function downloadBackup(id) {
  const response = await authenticatedFetch('/instance-backups/' + id + '/download-ticket', {
    method: 'POST',
  });
  if (!response.ok) return responseError(response);
  const payload = await response.json();
  if (!payload.ticket) throw new Error('Backend nie zwrócił biletu pobierania.');

  // Native form download streams the response directly to the browser download
  // manager. The one-time ticket stays in the POST body instead of a URL and
  // avoids materializing multi-gigabyte archives as JavaScript Blobs.
  const form = document.createElement('form');
  form.method = 'POST';
  form.action = API + '/instance-backups/' + id + '/download-browser';
  form.hidden = true;
  const input = document.createElement('input');
  input.type = 'hidden';
  input.name = 'ticket';
  input.value = payload.ticket;
  form.appendChild(input);
  document.body.appendChild(form);
  form.submit();
  setTimeout(() => form.remove(), 0);
}

function progressPanel(item, onManualDownload) {
  const failed = item?.status === 'failed';
  const ready = item?.download_ready === true;
  const panel = node('section', { class: 'panel instance-backup-progress' },
    node('div', { class: 'instance-backup-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Status' }),
        node('h3', { text: failed ? 'Backup nie został utworzony' : ready ? 'Backup gotowy' : 'Przygotowywanie backupu...' })),
      badge(failed ? 'Błąd' : (item?.progress || 0) + '%', failed ? 'danger' : ready ? 'ok' : 'warning')),
    stageList(item?.stage_code || 'queued', failed),
    failed
      ? node('p', { class: 'instance-backup-error', text: item.error || 'Operacja nie powiodła się.' })
      : ready
        ? node('div', { class: 'instance-backup-ready' },
            node('strong', { text: 'Backup jest gotowy.' }),
            node('span', { class: 'muted', text: item.filename || '' }),
            button('Pobierz backup', onManualDownload, 'primary'))
        : node('p', { class: 'muted', text: item?.stage || 'Oczekiwanie na worker.' })
  );
  return panel;
}

function sourceDetails(manifest, backup) {
  const source = manifest?.source || {};
  const app = manifest?.application || {};
  const secret = manifest?.secret || {};
  return node('div', { class: 'instance-backup-meta-grid' },
    metaItem('Źródłowa instancja', source.hostname),
    metaItem('Wersja', app.version),
    metaItem('Commit', app.commit, true),
    metaItem('Data backupu', manifest?.created_at),
    metaItem('Alembic revision', app.alembic_revision, true),
    metaItem('Secret backend', secret.backend),
    metaItem('Tryb instalacji', source.install_mode),
    metaItem('Rozmiar', formatBytes(backup?.size_bytes)));
}

function countRows(counts) {
  const labels = [
    ['users', 'Użytkownicy'],
    ['credentials', 'Credentiale'],
    ['providers', 'Providerzy'],
    ['projects', 'Projekty'],
    ['tenants', 'Tenanty'],
    ['blueprints', 'Blueprinty'],
    ['deployments', 'Deploymenty'],
    ['terraform_state', 'Terraform State'],
  ];
  return node('div', { class: 'instance-backup-counts' },
    ...labels.map(([key, label]) => node('div', {},
      node('span', { text: label }),
      node('strong', { text: String(counts?.[key] ?? 0) }))));
}

function currentMetadataPanel(meta, onCreate) {
  return node('section', { class: 'panel instance-backup-card' },
    node('div', { class: 'instance-backup-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Eksport' }),
        node('h2', { text: 'Backup tej instancji' }),
        node('p', { class: 'muted', text: 'Pobierz kompletny backup Cloudportal-backed zawierający bazę danych, ustawienia i materiał kryptograficzny wymagany do przeniesienia instancji.' })),
      badge('WEB UI', 'ok')),
    node('div', { class: 'instance-backup-meta-grid' },
      metaItem('Wersja aplikacji', meta.application_version),
      metaItem('Commit', meta.git_commit, true),
      metaItem('Alembic revision', meta.alembic_revision, true),
      metaItem('Tryb instalacji', meta.install_mode),
      metaItem('Secret backend', meta.secret_backend),
      metaItem('Hostname', meta.hostname)),
    node('div', { class: 'instance-backup-actions' },
      button('Utwórz i pobierz backup', onCreate, 'primary')));
}

function restorePanel(onPick, onDrop) {
  const input = node('input', { type: 'file', accept: '.cpb,application/octet-stream', hidden: true });
  input.addEventListener('change', () => {
    const file = input.files?.[0];
    if (file) onPick(file);
    input.value = '';
  });
  const drop = node('div', { class: 'instance-backup-drop', tabindex: '0' },
    node('strong', { text: 'Wybierz plik backupu .cpb' }),
    node('span', { class: 'muted', text: 'Przeciągnij plik tutaj albo wybierz go z komputera.' }),
    button('Wybierz plik .cpb', () => input.click(), 'primary'),
    input);
  ['dragenter', 'dragover'].forEach(name => drop.addEventListener(name, event => {
    event.preventDefault();
    drop.classList.add('dragging');
  }));
  ['dragleave', 'drop'].forEach(name => drop.addEventListener(name, event => {
    event.preventDefault();
    drop.classList.remove('dragging');
  }));
  drop.addEventListener('drop', event => {
    const file = event.dataTransfer?.files?.[0];
    if (file) onDrop(file);
  });

  return node('section', { class: 'panel instance-backup-card' },
    node('div', { class: 'instance-backup-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Import' }),
        node('h2', { text: 'Przywróć / przenieś instancję' }),
        node('p', { class: 'muted', text: 'Wgraj .cpb, zweryfikuj integralność i zobacz plan migracji przed destrukcyjnym restore.' }))),
    drop);
}

function planPanel(uploaded, onStart) {
  const safety = node('input', { type: 'checkbox', checked: true });
  const confirmation = node('input', {
    type: 'text',
    value: '',
    placeholder: 'RESTORE',
    autocomplete: 'off',
    spellcheck: false,
  });
  const preflightOk = uploaded.preflight?.ok !== false;
  const start = button('Rozpocznij migrację', () => onStart({
    confirmation: confirmation.value,
    safety_backup: safety.checked,
  }), 'danger');
  start.disabled = true;
  confirmation.addEventListener('input', () => {
    start.disabled = confirmation.value !== 'RESTORE' || !preflightOk || !allowed('instance_backups.restore');
  });

  const plan = uploaded.plan || {};
  const source = plan.source || {};
  const target = plan.target || {};
  return node('section', { class: 'panel instance-backup-plan' },
    node('div', { class: 'instance-backup-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Walidacja' }),
        node('h2', { text: 'Backup poprawny' }),
        node('p', { class: 'muted', text: uploaded.backup?.filename || '' })),
      badge('Zweryfikowany', 'ok')),
    sourceDetails(uploaded.manifest, uploaded.backup),
    node('hr'),
    node('h3', { text: 'Plan migracji' }),
    node('div', { class: 'instance-backup-compare' },
      node('div', {},
        node('span', { class: 'muted', text: 'Źródło' }),
        node('strong', { text: source.hostname || '—' }),
        node('span', { text: (source.install_mode || '—') + ' · ' + (uploaded.manifest?.application?.version || '—') })),
      node('div', {},
        node('span', { class: 'muted', text: 'Cel' }),
        node('strong', { text: target.hostname || '—' }),
        node('span', { text: (target.install_mode || '—') + ' · ' + (target.application_version || '—') }))),
    countRows(plan.counts),
    ...(plan.warnings || []).map(message =>
      node('p', { class: 'instance-backup-warning', text: 'Preflight: ' + message })),
    node('div', { class: 'instance-backup-preserved' },
      node('strong', { text: 'Ustawienia lokalne nowego serwera zostaną zachowane:' }),
      node('ul', {},
        ...(plan.preserved_local_settings || []).map(item => node('li', { text: item })))),
    node('label', { class: 'instance-backup-check' },
      safety,
      node('span', { text: 'Utwórz backup bezpieczeństwa aktualnej instancji przed restore' })),
    node('div', { class: 'instance-backup-confirm' },
      node('label', { text: 'Aby uruchomić restore, wpisz RESTORE' }),
      confirmation),
    node('div', { class: 'instance-backup-actions' }, start));
}

function restoreProgressPanel(item) {
  return node('section', { class: 'panel instance-backup-progress' },
    node('div', { class: 'instance-backup-section-head' },
      node('div', {},
        node('span', { class: 'tools-eyebrow', text: 'Restore' }),
        node('h2', { text: item.status === 'completed' ? 'Migracja zakończona' : item.status === 'failed' ? 'Migracja nie powiodła się' : 'Migracja w toku' }),
        node('p', { class: 'muted', text: item.stage || 'Oczekiwanie' })),
      badge(String(item.progress ?? 0) + '%', item.status === 'failed' ? 'danger' : item.status === 'completed' ? 'ok' : 'warning')),
    node('div', { class: 'instance-backup-restore-bar' },
      node('div', { style: 'width:' + Math.max(0, Math.min(100, Number(item.progress || 0))) + '%' })),
    item.message ? node('p', { text: item.message }) : null,
    item.rollback ? node('p', { class: 'muted', text: 'Rollback: ' + item.rollback }) : null,
    item.reauthentication_required
      ? node('p', { class: 'instance-backup-warning', text: 'Przywrócona baza nie zawiera bieżącej sesji. Zaloguj się danymi z instancji źródłowej.' })
      : null);
}

function backupTable(items, actions) {
  if (!items.length) return node('div', { class: 'empty', text: 'Brak utworzonych backupów.' });

  const rows = items.map(item => node('tr', {},
    node('td', { text: formatDate(item.created_at) }),
    node('td', {}, node('span', { class: 'mono', text: item.filename })),
    node('td', { text: (item.application_version || '—') + (item.git_commit ? ' @ ' + item.git_commit.slice(0, 7) : '') }),
    node('td', { text: formatBytes(item.size_bytes) }),
    node('td', {}, node('span', {
      class: 'mono',
      text: item.sha256 ? 'SHA256: ' + item.sha256.slice(0, 12) + '…' : '—',
    })),
    node('td', {}, badge(
      item.status,
      item.download_ready ? 'ok' : item.status === 'failed' ? 'danger' : 'warning',
    )),
    node('td', { text: formatDate(item.expires_at) }),
    node('td', {},
      node('div', { class: 'instance-backup-row-actions' },
        button('Pobierz', () => actions.download(item), '', !item.download_ready),
        button('Zweryfikuj', () => actions.verify(item), '', !item.download_ready),
        button(actions.deleteLabel(item), () => actions.remove(item), 'danger')))));

  return node('div', { class: 'table-wrap instance-backup-table-wrap' },
    node('table', { class: 'table instance-backup-table' },
      node('thead', {},
        node('tr', {},
          ...['Data', 'Plik', 'Wersja', 'Rozmiar', 'Checksum', 'Status', 'Wygasa', 'Akcje']
            .map(text => node('th', { text })))),
      node('tbody', {}, ...rows)));
}

async function instanceBackupView() {
  const meta = await api('/instance-backups/metadata');
  const stateView = {
    progress: node('div'),
    restoreArea: node('div'),
    recent: node('div'),
    deleteArmed: null,
    createBusy: false,
  };

  async function refreshRecent() {
    const data = await api('/instance-backups');
    stateView.recent.replaceChildren(
      node('section', { class: 'panel instance-backup-recent' },
        node('div', { class: 'instance-backup-section-head' },
          node('div', {},
            node('span', { class: 'tools-eyebrow', text: 'Retencja' }),
            node('h2', { text: 'Ostatnio utworzone backupy' }),
            node('p', { class: 'muted', text: 'Pliki do pobrania są przechowywane przez ' + meta.download_retention_hours + ' h.' }))),
        backupTable(data.items || [], {
          download: async item => {
            try { await downloadBackup(item.id, item.filename); }
            catch (error) { toast(error.message, 'error'); }
          },
          verify: async item => {
            try {
              await api('/instance-backups/' + item.id + '/verify', { method: 'POST', body: {} });
              toast('Integralność backupu została zweryfikowana.');
            } catch (error) { toast(error.message, 'error'); }
          },
          deleteLabel: item => stateView.deleteArmed === item.id ? 'Potwierdź usunięcie' : 'Usuń',
          remove: async item => {
            if (stateView.deleteArmed !== item.id) {
              stateView.deleteArmed = item.id;
              await refreshRecent();
              return;
            }
            try {
              await api('/instance-backups/' + item.id, { method: 'DELETE' });
              stateView.deleteArmed = null;
              await refreshRecent();
            } catch (error) { toast(error.message, 'error'); }
          },
        })));
  }

  async function pollBackup(id) {
    while (document.body.contains(stateView.progress)) {
      const item = await api('/instance-backups/' + id);
      stateView.progress.replaceChildren(progressPanel(item, () => downloadBackup(item.id, item.filename)));
      if (item.status === 'failed') {
        stateView.createBusy = false;
        await refreshRecent();
        return;
      }
      if (item.download_ready) {
        stateView.createBusy = false;
        try {
          await downloadBackup(item.id, item.filename);
        } catch (error) {
          toast('Backup jest gotowy. Automatyczne pobranie nie powiodło się; użyj przycisku Pobierz backup.', 'error');
        }
        await refreshRecent();
        return;
      }
      await delay(900);
    }
  }

  async function createBackup() {
    if (stateView.createBusy) return;
    stateView.createBusy = true;
    try {
      const created = await api('/instance-backups', { method: 'POST', body: {} });
      stateView.progress.replaceChildren(progressPanel({
        status: 'queued',
        progress: 0,
        stage_code: 'queued',
        stage: 'Oczekiwanie',
      }, () => {}));
      await pollBackup(created.id);
    } catch (error) {
      stateView.createBusy = false;
      toast(error.message, 'error');
    }
  }

  async function uploadFile(file) {
    if (!file?.name?.toLowerCase().endsWith('.cpb')) {
      toast('Wybierz plik z rozszerzeniem .cpb.', 'error');
      return;
    }
    stateView.restoreArea.replaceChildren(
      node('section', { class: 'panel instance-backup-progress' },
        node('h2', { text: 'Wysyłanie i walidacja backupu...' }),
        node('p', { class: 'muted', text: file.name + ' · ' + formatBytes(file.size) })));
    const form = new FormData();
    form.append('file', file, file.name);
    try {
      const response = await authenticatedFetch('/instance-backups/upload', { method: 'POST', body: form });
      if (!response.ok) await responseError(response);
      const uploaded = await response.json();
      stateView.restoreArea.replaceChildren(planPanel(uploaded, data => startRestore(uploaded.backup.id, data)));
    } catch (error) {
      stateView.restoreArea.replaceChildren(
        node('section', { class: 'panel instance-backup-progress' },
          node('h2', { text: 'Backup odrzucony' }),
          node('p', { class: 'instance-backup-error', text: error.message })));
    }
  }

  async function pollRestore(restoreUuid) {
    while (document.body.contains(stateView.restoreArea)) {
      try {
        const item = await api('/instance-backups/restores/' + encodeURIComponent(restoreUuid));
        stateView.restoreArea.replaceChildren(restoreProgressPanel(item));
        if (['completed', 'failed'].includes(item.status)) {
          await refreshRecent().catch(() => {});
          return;
        }
      } catch (error) {
        if (error.status === 401) {
          stateView.restoreArea.replaceChildren(
            node('section', { class: 'panel instance-backup-progress' },
              node('h2', { text: 'Sesja została zastąpiona przez przywróconą instancję' }),
              node('p', { class: 'muted', text: 'Zaloguj się danymi z instancji źródłowej, aby sprawdzić wynik restore.' })));
          showLogin('Zaloguj się danymi z przywróconej instancji.');
          return;
        }
        throw error;
      }
      await delay(1000);
    }
  }

  async function startRestore(backupId, data) {
    if (data.confirmation !== 'RESTORE') return;
    try {
      const started = await api('/instance-backups/' + backupId + '/restore', {
        method: 'POST',
        body: data,
      });
      stateView.restoreArea.replaceChildren(restoreProgressPanel({
        status: 'queued',
        stage: 'Oczekiwanie',
        progress: 0,
      }));
      await pollRestore(started.restore_uuid);
    } catch (error) {
      toast(error.message, 'error');
    }
  }

  const restore = restorePanel(uploadFile, uploadFile);
  dom.content.replaceChildren(
    heading('Narzędzia / Backup i migracja', [button('← Narzędzia', () => navigate('tools'))]),
    node('div', { class: 'instance-backup-layout' },
      currentMetadataPanel(meta, createBackup),
      restore),
    stateView.progress,
    stateView.restoreArea,
    stateView.recent
  );
  await refreshRecent();
}

registerView({
  id: 'instance-backup',
  label: 'Backup i migracja',
  iconName: 'file-text',
  navigation: false,
  navigationParent: 'tools',
  permission: 'instance_backups.read',
  order: 156,
}, instanceBackupView);
})();
