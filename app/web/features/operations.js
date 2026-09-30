'use strict';

(() => {
async function schedulesView() {
  const schedules = (await api('/schedules?limit=200')).items;
  const actions = allowed('schedules.create') && allowed('deployments.read') ? [button('Nowy harmonogram', () => navigate('/operations/schedules/new'), 'primary')] : [];
  dom.content.replaceChildren(heading('Trwałe operacje Terraform uruchamiane przez dispatcher z ponowną kontrolą uprawnień.', actions),
    table([
      { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
      { label: 'Operacja', value: item => operationLabel(item.operation) },
      { label: 'Wdrożenie', class: 'mono', value: item => short(item.deployment_id, 18) },
      { label: 'Następne', value: item => formatDate(item.next_run_at) },
      { label: 'Interwał', value: item => item.interval_seconds ? formatDuration(item.interval_seconds) : 'Jednorazowo' },
      { label: 'Status', value: item => badge(statusLabel(item.is_active ? 'active' : 'disabled'), item.is_active ? 'ok' : 'info') },
      { label: 'Błąd', value: item => item.last_error || '—' },
    ], schedules, item => {
      const result = [];
      if (allowed('schedules.update')) {
        if (allowed('deployments.read')) result.push(button(item.is_active ? 'Edytuj' : 'Włącz i edytuj', () => navigate('/operations/schedules/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'schedule'))));
        if (item.is_active) result.push(button('Wyłącz', async () => { await api(`/schedules/${item.id}/disable`, { method: 'POST' }); navigate('schedules'); }));
      }
      if (allowed('schedules.delete')) result.push(button('Usuń', () => confirmAction('Usuń harmonogram', item.name, async () => {
        await api(`/schedules/${item.id}`, { method: 'DELETE' }); navigate('schedules');
      }), 'danger'));
      return result;
    }));
}

async function scheduleForm(item = null) {
  try {
    const deployments = (await api('/deployments?limit=200')).items.filter(row => row.status !== 'destroyed');
    const dateValue = toDateTimeLocal(item?.next_run_at || new Date(Date.now() + 3600000));
    const commonIntervals = new Set([3600, 21600, 43200, 86400, 604800]);
    const currentInterval = Number(item?.interval_seconds || 0);
    const presetValue = currentInterval && !commonIntervals.has(currentInterval) ? 'custom' : String(currentInterval || '');
    const recurrenceField = selectField('Powtarzanie', 'interval_preset', [
      { value: '', label: 'Jednorazowo' },
      { value: '3600', label: 'Co godzinę' },
      { value: '21600', label: 'Co 6 godzin' },
      { value: '43200', label: 'Co 12 godzin' },
      { value: '86400', label: 'Codziennie' },
      { value: '604800', label: 'Co tydzień' },
      { value: 'custom', label: 'Własny interwał' },
    ], presetValue);
    const customInterval = field('Własny interwał (sekundy)', 'interval_seconds', {
      type: 'number', min: 60, value: presetValue === 'custom' ? currentInterval : '',
      help: 'Minimum 60 sekund.',
    });
    const refreshRecurrence = () => { customInterval.hidden = recurrenceField.querySelector('select').value !== 'custom'; };
    recurrenceField.querySelector('select').addEventListener('change', refreshRecurrence);
    refreshRecurrence();

    const fields = node('div', { class: 'form-grid' },
      field('Nazwa', 'name', { required: true, value: item?.name || '' }),
      selectField('Wdrożenie', 'deployment_id', deployments.map(row => ({ value: row.id, label: `${row.name} · ${short(row.id, 10)}` })), item?.deployment_id || '', { required: true, placeholder: 'Wybierz wdrożenie' }),
      selectField('Operacja', 'operation', [{ value: 'terraform.plan', label: 'Plan' }, { value: 'terraform.apply', label: 'Zastosuj' }, { value: 'terraform.destroy', label: 'Usuń zasoby' }], item?.operation || 'terraform.plan'),
      field('Następne uruchomienie', 'next_run_at', { type: 'datetime-local', required: true, value: dateValue }),
      recurrenceField,
      customInterval);
    openModal({ title: item ? 'Edytuj harmonogram' : 'Nowy harmonogram', eyebrow: 'Harmonogram', body: fields, onSubmit: async data => {
      const preset = data.get('interval_preset');
      const intervalSeconds = preset === 'custom'
        ? Number(data.get('interval_seconds'))
        : (preset ? Number(preset) : null);
      if (preset === 'custom' && (!Number.isFinite(intervalSeconds) || intervalSeconds < 60)) {
        throw new Error('Własny interwał musi mieć co najmniej 60 sekund.');
      }
      await api(item ? `/schedules/${item.id}` : '/schedules', { method: item ? 'PUT' : 'POST', body: {
        name: data.get('name'), deployment_id: data.get('deployment_id'), operation: data.get('operation'),
        next_run_at: new Date(data.get('next_run_at')).toISOString(),
        interval_seconds: intervalSeconds,
      } });
      toast('Harmonogram zapisany.');
      navigate('schedules');
    }});
  } catch (error) { toast(error.message, 'error'); }
}

async function webhooksView() {
  const [hooks, deliveries] = await Promise.all([api('/webhooks?limit=200'), api('/webhook-deliveries?limit=200')]);
  const actions = allowed('webhooks.create') ? [button('Nowy webhook', () => navigate('/operations/webhooks/new'), 'primary')] : [];
  dom.content.replaceChildren(heading('Podpisane HMAC dostawy HTTPS. Host musi znajdować się w allowliście backendu.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Endpointy' })),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'URL', class: 'mono', value: item => short(item.url, 48) },
        { label: 'Zdarzenia', value: item => item.events.map(webhookEventLabel).join(', ') },
        { label: 'Status', value: item => badge(item.is_active ? 'active' : 'inactive', item.is_active ? 'ok' : 'danger') },
      ], hooks.items, item => {
        const result = [];
        if (allowed('webhooks.update')) {
          result.push(button('Edytuj', () => navigate('/operations/webhooks/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'webhook'))));
          result.push(button('Rotuj sekret', async () => {
            const value = await api(`/webhooks/${item.id}/rotate-secret`, { method: 'POST' });
            showSecret('Nowy webhook secret', value.secret);
          }));
        }
        if (allowed('webhooks.delete')) result.push(button('Usuń', () => confirmAction('Usuń webhook', item.name, async () => {
          await api(`/webhooks/${item.id}`, { method: 'DELETE' }); navigate('webhooks');
        }), 'danger'));
        return result;
      })
    ),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('h2', { text: 'Dostawy' })),
      table([
        { label: 'Zdarzenie', value: item => webhookEventLabel(item.event) },
        { label: 'Zasób', class: 'mono', value: item => short(item.resource_id, 18) },
        { label: 'Status', value: item => badge(item.status, statusKind(item.status)) },
        { label: 'Próby', value: item => item.attempts },
        { label: 'Następna', value: item => formatDate(item.next_attempt_at) },
        { label: 'Błąd', value: item => item.last_error || '—' },
      ], deliveries.items)
    )
  );
}

function webhookForm(item = null) {
  const events = item?.events || ['job.successful', 'job.failed'];
  const fields = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', { required: true, value: item?.name || '' }),
    field('HTTPS URL', 'url', { required: true, value: item?.url || '', wide: true }),
    checkboxField('Zadanie zakończone', 'event_job_successful', events.includes('job.successful')),
    checkboxField('Zadanie zakończone błędem', 'event_job_failed', events.includes('job.failed')),
    checkboxField('Zadanie anulowane', 'event_job_cancelled', events.includes('job.cancelled')),
    checkboxField('Odzyskiwanie dodane do kolejki', 'event_recovery_queued', events.includes('recovery.queued')),
    checkboxField('Odzyskiwanie zakończone', 'event_recovery_successful', events.includes('recovery.successful')),
    checkboxField('Odzyskiwanie zakończone błędem', 'event_recovery_failed', events.includes('recovery.failed')),
    checkboxField('Alert systemowy', 'event_system_alert', events.includes('system.alert')),
    checkboxField('Aktywny', 'is_active', item?.is_active ?? true));
  openModal({ title: item ? 'Edytuj webhook' : 'Nowy webhook', eyebrow: 'Signed HMAC', body: fields, onSubmit: async data => {
    const selected = [];
    for (const event of ['job.successful', 'job.failed', 'job.cancelled', 'recovery.queued', 'recovery.successful', 'recovery.failed', 'system.alert']) {
      if (data.has('event_' + event.replace('.', '_'))) selected.push(event);
    }
    if (!selected.length) throw new Error('Wybierz co najmniej jeden event.');
    const result = await api(item ? `/webhooks/${item.id}` : '/webhooks', {
      method: item ? 'PUT' : 'POST', idempotent: !item, body: {
        name: data.get('name'), url: data.get('url'), events: selected, is_active: data.has('is_active'),
      },
    });
    if (!item && result.secret) {
      navigate('webhooks');
      showSecret('Webhook secret', result.secret);
    } else {
      toast('Webhook zapisany.');
      navigate('webhooks');
    }
    return item ? true : false;
  }});
}

registerRoutedForm({
  id: 'schedules-create',
  pattern: /^\/operations\/schedules\/new$/,
  parent: 'schedules',
  permission: 'schedules.create',
  label: 'Harmonogramy',
}, () => scheduleForm());
registerRoutedForm({
  id: 'schedules-edit',
  pattern: /^\/operations\/schedules\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'schedules',
  permission: 'schedules.update',
  label: 'Harmonogramy',
}, async match => {
  const rows = (await api('/schedules?limit=200')).items;
  const item = rows.find(value => Number(value.id) === Number(match.params.id));
  if (!item) throw new Error('Nie znaleziono harmonogramu.');
  await scheduleForm(item);
});
registerRoutedForm({
  id: 'webhooks-create',
  pattern: /^\/operations\/webhooks\/new$/,
  parent: 'webhooks',
  permission: 'webhooks.create',
  label: 'Webhooki',
}, () => webhookForm());
registerRoutedForm({
  id: 'webhooks-edit',
  pattern: /^\/operations\/webhooks\/edit\/(?<id>\d+)(?:\/[^/]+)?$/,
  parent: 'webhooks',
  permission: 'webhooks.update',
  label: 'Webhooki',
}, async match => {
  const rows = (await api('/webhooks?limit=200')).items;
  const item = rows.find(value => Number(value.id) === Number(match.params.id));
  if (!item) throw new Error('Nie znaleziono webhooka.');
  webhookForm(item);
});
registerView({ id: 'schedules', label: 'Harmonogramy', icon: 'S', permission: 'schedules.read', order: 130 }, schedulesView);
registerView({ id: 'webhooks', label: 'Webhooki', icon: 'W', permission: 'webhooks.read', order: 140 }, webhooksView);
})();
