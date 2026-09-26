'use strict';

(() => {
function taskTypeLabel(value, fallback = 'Operacja Proxmox') {
  const labels = {
    qmstart: 'Uruchomienie VM',
    qmstop: 'Wymuszone zatrzymanie VM',
    qmshutdown: 'Bezpieczne wyłączenie VM',
    qmreboot: 'Restart VM',
    qmreset: 'Twardy reset VM',
    qmsuspend: 'Wstrzymanie VM',
    qmresume: 'Wznowienie VM',
    qmclone: 'Klonowanie VM',
    qmmigrate: 'Migracja VM',
    qmtemplate: 'Konwersja VM do template',
    vncproxy: 'Sesja konsoli',
    vzdump: 'Backup VM',
  };
  return labels[String(value || '').toLowerCase()] || fallback;
}

function taskTime(value) {
  if (!value) return '—';
  return new Date(Number(value) * 1000).toLocaleString('pl-PL');
}

function taskDuration(start, end) {
  const started = Number(start || 0);
  const finished = Number(end || 0);
  if (!started || !finished || finished < started) return '—';
  const seconds = Math.max(0, Math.round(finished - started));
  if (seconds < 60) return seconds + ' s';
  return Math.floor(seconds / 60) + ' min ' + String(seconds % 60).padStart(2, '0') + ' s';
}

function summaryCard(label, valueNode) {
  return node('div', { class: 'task-summary-card' },
    node('span', { text: label }),
    valueNode);
}

async function show(item, result, title) {
  if (!result?.task) {
    toast(`${title}: operacja została przyjęta.`);
    return;
  }

  stopTaskPolling();
  const nonce = state.taskPollNonce;

  const statusValue = node('strong', { text: 'Uruchamianie…' });
  const exitValue = node('strong', { text: 'W trakcie' });
  const typeValue = node('strong', { text: title || 'Operacja Proxmox' });
  const startedValue = node('strong', { text: '—' });
  const finishedValue = node('strong', { text: '—' });
  const durationValue = node('strong', { text: '—' });
  const rawTypeValue = node('code', { class: 'mono', text: '—' });

  const resultIcon = node('div', { class: 'task-result-icon is-running', 'aria-hidden': 'true' },
    node('div', { class: 'spinner' }));
  const resultTitle = node('strong', { class: 'task-result-title', text: 'Operacja w toku' });
  const resultDescription = node('p', {
    class: 'task-result-description',
    text: 'Cloudportal oczekuje na zakończenie zadania w Proxmox.',
  });
  const resultState = node('section', { class: 'task-result-state' },
    resultIcon,
    node('div', { class: 'task-result-copy' }, resultTitle, resultDescription));

  const technicalDetails = node('details', { class: 'task-id-details task-technical-details' },
    node('summary', { text: 'Szczegóły techniczne' }),
    node('div', { class: 'task-technical-grid' },
      node('div', { class: 'task-technical-row' },
        node('span', { text: 'Typ Proxmox' }),
        rawTypeValue),
      node('div', { class: 'task-technical-row' },
        node('span', { text: 'UPID' }),
        node('code', { class: 'mono task-upid', text: String(result.task) }))));

  dom.modal.classList.remove('modal-console', 'modal-wide');
  dom.modalTitle.textContent = title;
  dom.modalEyebrow.textContent = [
    item.name || 'VM',
    item.node || '—',
    'VMID ' + item.vm_id,
  ].join(' · ');

  dom.modalBody.replaceChildren(
    node('div', { class: 'task-result-shell' },
      resultState,
      node('section', { class: 'task-summary-grid' },
        summaryCard('Status', statusValue),
        summaryCard('Wynik', exitValue),
        summaryCard('Operacja', typeValue),
        summaryCard('Rozpoczęcie', startedValue),
        summaryCard('Zakończenie', finishedValue),
        summaryCard('Czas trwania', durationValue)),
      technicalDetails));

  dom.modalActions.replaceChildren(button('Zamknij', closeModal, 'primary'));
  if (!(typeof window.modalSurfaceOpen === 'function' ? window.modalSurfaceOpen() : dom.modal.open)) {
    dom.modal.showModal();
  }

  const poll = async () => {
    const modalOpen = typeof window.modalSurfaceOpen === 'function'
      ? window.modalSurfaceOpen()
      : dom.modal.open;
    if (nonce !== state.taskPollNonce || !modalOpen) return;

    try {
      const task = await api(
        `/providers/${item.provider_id}/tasks/${encodeURIComponent(item.node)}/${encodeURIComponent(String(result.task))}`
      );
      const stopped = task.status === 'stopped' || Boolean(task.exitstatus);
      const success = stopped && (!task.exitstatus || task.exitstatus === 'OK');

      statusValue.textContent = stopped ? 'Zakończone' : statusLabel(task.status || 'running');
      exitValue.textContent = task.exitstatus || (stopped ? '—' : 'W trakcie');
      typeValue.textContent = taskTypeLabel(task.type, title || 'Operacja Proxmox');
      rawTypeValue.textContent = task.type || '—';
      startedValue.textContent = taskTime(task.starttime);
      finishedValue.textContent = taskTime(task.endtime);
      durationValue.textContent = taskDuration(task.starttime, task.endtime);

      if (stopped) {
        resultIcon.className = 'task-result-icon ' + (success ? 'is-success' : 'is-danger');
        resultIcon.replaceChildren(success
          ? appIcon('check')
          : node('strong', { class: 'task-result-error-symbol', text: '!' }));
        resultState.className = 'task-result-state ' + (success ? 'is-success' : 'is-danger');
        resultTitle.textContent = success ? 'Operacja zakończona' : 'Operacja zakończona błędem';
        resultDescription.textContent = success
          ? taskTypeLabel(task.type, title) + ' została zakończona pomyślnie.'
          : 'Proxmox zwrócił wynik: ' + (task.exitstatus || 'błąd operacji') + '.';

        if (success) toast(`${title}: zakończono.`);
        else toast(`${title}: ${task.exitstatus || 'błąd operacji'}.`, 'error');
        state.taskPollTimer = null;
        return;
      }

      resultState.className = 'task-result-state';
      resultTitle.textContent = 'Operacja w toku';
      resultDescription.textContent = taskTypeLabel(task.type, title) + ' jest wykonywana przez Proxmox.';
      state.taskPollTimer = window.setTimeout(poll, 1500);
    } catch (error) {
      resultState.className = 'task-result-state is-warning';
      resultTitle.textContent = 'Nie udało się odświeżyć statusu';
      resultDescription.textContent = error.message;
      state.taskPollTimer = window.setTimeout(poll, 4000);
    }
  };

  await poll();
}

window.InventoryTaskModal = Object.freeze({ show });
registerExtension('inventory-task-modal', () => {});
})();
