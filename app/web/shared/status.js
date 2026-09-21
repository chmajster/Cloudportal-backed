'use strict';

(() => {
  const STATUS = Object.freeze({
    ok: { label: 'OK', kind: 'ok' },
    active: { label: 'Aktywny', kind: 'ok' },
    configured: { label: 'Skonfigurowany', kind: 'ok' },
    available: { label: 'Dostępny', kind: 'ok' },
    successful: { label: 'Zakończone', kind: 'ok' },
    success: { label: 'Sukces', kind: 'ok' },
    running: { label: 'W toku', kind: 'warning' },
    queued: { label: 'W kolejce', kind: 'warning' },
    pending: { label: 'Oczekuje', kind: 'warning' },
    pending_approval: { label: 'Czeka na akceptację', kind: 'warning' },
    waiting_provider: { label: 'Czeka na platformę', kind: 'warning' },
    retrying: { label: 'Ponawianie', kind: 'warning' },
    cancelling: { label: 'Anulowanie', kind: 'warning' },
    degraded: { label: 'Zdegradowany', kind: 'warning' },
    failed: { label: 'Błąd', kind: 'danger' },
    failure: { label: 'Błąd', kind: 'danger' },
    critical: { label: 'Krytyczny', kind: 'danger' },
    locked: { label: 'Zablokowany', kind: 'danger' },
    revoked: { label: 'Unieważniony', kind: 'danger' },
    inactive: { label: 'Nieaktywny', kind: 'danger' },
    down: { label: 'Niedostępny', kind: 'danger' },
    cancelled: { label: 'Anulowane', kind: 'info' },
    stopped: { label: 'Zatrzymany', kind: 'info' },
    external: { label: 'Zewnętrzny', kind: 'info' },
  });

  function statusMeta(value) {
    const key = String(value || '').trim().toLowerCase();
    return STATUS[key] || null;
  }

  window.uiStatusMeta = statusMeta;
  window.uiStatusLabel = value => statusMeta(value)?.label || null;
  window.uiStatusKind = value => statusMeta(value)?.kind || null;
  window.uiStatusBadge = value => {
    const meta = statusMeta(value);
    return badge(meta?.label || String(value || '—'), meta?.kind || 'info');
  };
})();
