'use strict';

(() => {
function scopeHeaders(scope) {
  if (!scope?.tenant_id || !scope?.project_id) return {};
  return {
    'X-Tenant-ID': String(scope.tenant_id),
    'X-Project-ID': String(scope.project_id),
  };
}

function resourceHeaders(item) {
  return scopeHeaders({
    tenant_id: item?.tenant_id,
    project_id: item?.project_id,
  });
}

async function currentScope() {
  const result = await api('/project-context');
  const selected = result?.selected || null;
  if (!selected) {
    return {
      tenant_id: null,
      project_id: null,
      tenant_name: 'Default',
      project_name: 'Default',
      headers: {},
    };
  }
  return {
    tenant_id: selected.tenant_id,
    project_id: selected.id,
    tenant_name: result.tenant_name || selected.tenant_id,
    project_name: selected.name || selected.slug || selected.id,
    headers: scopeHeaders({ tenant_id: selected.tenant_id, project_id: selected.id }),
  };
}

function stateLabel(value) {
  return {
    started: 'Uruchomiona / HA aktywne',
    stopped: 'Zatrzymana',
    ignored: 'Ignorowana przez HA',
    disabled: 'HA wyłączone',
  }[value] || value || '—';
}

function assignmentStatus(value) {
  const labels = {
    pending: ['Oczekuje', 'warning'],
    queued: ['W kolejce', 'warning'],
    running: ['Stosowanie', 'warning'],
    waiting_approval: ['Czeka na akceptację', 'warning'],
    applying: ['Stosowanie', 'warning'],
    applied: ['Zastosowany', 'ok'],
    successful: ['Zastosowany', 'ok'],
    failed: ['Błąd', 'danger'],
    cancelled: ['Anulowany', 'danger'],
  };
  return labels[value] || [value || 'Brak', ''];
}

function planForm(item, headers, onSaved) {
  const body = node('div', { class: 'form-grid' },
    field('Nazwa', 'name', {
      value: item?.name || '',
      required: true,
      maxlength: 100,
      help: 'Nazwa musi być unikalna tylko w bieżącej Organizacji i Projekcie.',
    }),
    selectField('Stan HA', 'state', [
      { value: 'started', label: 'started — VM ma być utrzymywana przez HA' },
      { value: 'stopped', label: 'stopped — VM ma pozostać zatrzymana' },
      { value: 'ignored', label: 'ignored — HA ignoruje VM' },
      { value: 'disabled', label: 'disabled — wpis HA wyłączony' },
    ], item?.state || 'started'),
    field('Grupa HA', 'group', {
      value: item?.group || '',
      placeholder: 'opcjonalnie, np. production',
      help: 'Opcjonalna grupa HA. W Proxmox VE 9 po migracji grup do HA rules pozostaw to pole puste.',
    }),
    field('Max restart', 'max_restart', {
      type: 'number', min: 0, max: 100, value: item?.max_restart ?? 1, required: true,
      help: 'Maksymalna liczba lokalnych prób restartu przed relokacją.',
    }),
    field('Max relocate', 'max_relocate', {
      type: 'number', min: 0, max: 100, value: item?.max_relocate ?? 1, required: true,
      help: 'Maksymalna liczba prób relokacji usługi HA.',
    }),
    field('Opis', 'description', {
      tag: 'textarea',
      wide: true,
      value: item?.description || '',
      maxlength: 2000,
      placeholder: 'Przeznaczenie planu, wymagania SLA, uwagi administracyjne…',
    }),
    checkboxField('Plan aktywny i dostępny do przypisywania', 'is_active', item ? item.is_active !== false : true),
  );

  openModal({
    title: item ? 'Edytuj Availability Plan' : 'Nowy Availability Plan',
    eyebrow: 'Proxmox HA',
    body,
    submitLabel: item ? 'Zapisz' : 'Utwórz plan',
    wide: true,
    onSubmit: async data => {
      const payload = {
        name: String(data.get('name') || '').trim(),
        description: String(data.get('description') || '').trim(),
        state: String(data.get('state') || 'started'),
        group: String(data.get('group') || '').trim() || null,
        max_restart: Number(data.get('max_restart')),
        max_relocate: Number(data.get('max_relocate')),
        is_active: data.has('is_active'),
      };
      await api('/availability-plans' + (item ? '/' + encodeURIComponent(item.id) : ''), {
        method: item ? 'PUT' : 'POST',
        body: payload,
        headers,
      });
      toast(item ? 'Availability Plan zaktualizowany.' : 'Availability Plan utworzony.');
      if (onSaved) await onSaved();
    },
  });
}

async function availabilityPlansView() {
  const scope = await currentScope();
  const result = await api('/availability-plans?limit=200', { headers: scope.headers });
  const plans = result.items || [];
  const scopedPermissions = result.permissions || [];
  const scopeAllows = permission => allowed(permission) || scopedPermissions.includes(permission);
  const actions = [];

  if (scopeAllows('availability.create')) {
    actions.push(button('Nowy plan', () => planForm(null, scope.headers, availabilityPlansView), 'primary'));
  }

  const context = node('section', { class: 'panel' },
    node('div', { class: 'panel-header' },
      node('div', {},
        node('h2', { text: 'Bieżący zakres' }),
        node('p', { class: 'muted', text: 'Availability Plans są izolowane per Organizacja + Projekt.' }))),
    node('div', { class: 'tool-meta-grid' },
      node('div', { class: 'tool-meta-item' }, node('span', { text: 'Organizacja' }), node('strong', { text: scope.tenant_name })),
      node('div', { class: 'tool-meta-item' }, node('span', { text: 'Projekt' }), node('strong', { text: scope.project_name })),
      node('div', { class: 'tool-meta-item' }, node('span', { text: 'Planów' }), node('strong', { text: String(plans.length) })),
      node('div', { class: 'tool-meta-item' }, node('span', { text: 'Aktywnych' }), node('strong', { text: String(plans.filter(plan => plan.is_active).length) }))));

  const list = plans.length
    ? table([
      { label: 'Nazwa', value: plan => node('strong', { text: plan.name }) },
      { label: 'Stan HA', value: plan => stateLabel(plan.state) },
      { label: 'Grupa HA', value: plan => plan.group || '—' },
      { label: 'Max restart', value: plan => String(plan.max_restart) },
      { label: 'Max relocate', value: plan => String(plan.max_relocate) },
      { label: 'Status', value: plan => plan.is_active ? badge('Aktywny', 'ok') : badge('Wyłączony', 'warning') },
    ], plans, plan => {
      const rowActions = [];
      if (scopeAllows('availability.update')) {
        rowActions.push(button('Edytuj', () => planForm(plan, scope.headers, availabilityPlansView)));
      }
      if (scopeAllows('availability.delete')) {
        rowActions.push(button('Usuń', () => confirmAction(
          'Usuń Availability Plan',
          `Plan „${plan.name}” zostanie usunięty. Przypisanego planu nie można usunąć — najpierw należy zmienić przypisania.`,
          async () => {
            await api('/availability-plans/' + encodeURIComponent(plan.id), {
              method: 'DELETE',
              headers: scope.headers,
            });
            toast('Availability Plan usunięty.');
            await availabilityPlansView();
          },
        ), 'danger'));
      }
      return rowActions;
    })
    : node('div', { class: 'empty', text: 'Brak Availability Planów w bieżącej Organizacji i Projekcie.' });

  dom.content.replaceChildren(
    heading('Plany dostępności VM oparte o Proxmox HA.', actions),
    context,
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Availability Plans' }),
          node('p', { class: 'muted', text: 'Plan zapisuje desired state HA: stan, grupę, max_restart i max_relocate.' }))),
      list),
  );
}

async function card() {
  try {
    const scope = await currentScope();
    const result = await api('/availability-plans?active_only=true&limit=200', { headers: scope.headers });
    const plans = result.items || [];
    return node('article', { class: 'panel tool-card tool-card-featured' },
      node('div', { class: 'tool-card-head' },
        node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('shield')),
        node('div', { class: 'tool-title' },
          node('span', { class: 'tool-category', text: 'Proxmox HA' }),
          node('h2', { text: 'Availability Plan' }),
          node('p', { class: 'muted', text: 'Definiuj plany HA i przypisuj je podczas tworzenia VM albo później z widoku VM.' })),
        badge(plans.length ? `${plans.length} aktywnych` : 'Brak planów', plans.length ? 'ok' : 'warning')),
      node('div', { class: 'tool-card-footer' },
        node('span', { class: 'tool-health' },
          node('span', { class: 'status-dot ' + (plans.length ? 'ok' : 'warn') }),
          `${scope.tenant_name} / ${scope.project_name}`),
        button(plans.length ? 'Otwórz plany' : 'Skonfiguruj', () => navigate('availability-plans'), 'primary')));
  } catch (error) {
    if (error?.status === 403 || error?.status === 404) return null;
    return node('article', { class: 'panel tool-card' },
      node('div', { class: 'tool-card-head' },
        node('div', { class: 'tool-icon', 'aria-hidden': 'true' }, appIcon('shield')),
        node('div', { class: 'tool-title' },
          node('span', { class: 'tool-category', text: 'Proxmox HA' }),
          node('h2', { text: 'Availability Plan' }),
          node('p', { class: 'muted', text: 'Plany dostępności VM.' })),
        badge('Niedostępny', 'warning')),
      node('p', { class: 'tool-error muted', text: error.message }),
      node('div', { class: 'tool-card-footer' },
        node('span', { class: 'tool-health' }, node('span', { class: 'status-dot warn' }), 'Nie udało się pobrać planów'),
        button('Otwórz', () => navigate('availability-plans'), 'primary')));
  }
}

async function vmContent(item) {
  const headers = resourceHeaders(item);
  const [assignmentResult, plansResult] = await Promise.all([
    api('/availability-plans/resources/' + encodeURIComponent(item.id), { headers }),
    api('/availability-plans?active_only=true&limit=200', { headers }),
  ]);
  const assignment = assignmentResult.assignment || null;
  const plans = plansResult.items || [];
  const scopedPermissions = plansResult.permissions || [];
  const scopeAllows = permission => allowed(permission) || scopedPermissions.includes(permission);
  const status = assignmentStatus(assignment?.status);

  const current = node('section', { class: 'panel' },
    node('div', { class: 'panel-header' },
      node('div', {},
        node('h2', { text: 'Bieżący Availability Plan' }),
        node('p', { class: 'muted', text: 'Stan CloudPortal jest uzgadniany z rzeczywistą konfiguracją Proxmox HA.' })),
      assignment ? badge(status[0], status[1]) : badge('Nieprzypisany', 'info')),
    assignment
      ? node('div', { class: 'tool-meta-grid' },
        node('div', { class: 'tool-meta-item' }, node('span', { text: 'Plan' }), node('strong', { text: assignment.plan_name || '—' })),
        node('div', { class: 'tool-meta-item' }, node('span', { text: 'Stan HA' }), node('strong', { text: stateLabel(assignment.plan_snapshot?.state) })),
        node('div', { class: 'tool-meta-item' }, node('span', { text: 'Grupa' }), node('strong', { text: assignment.plan_snapshot?.group || '—' })),
        node('div', { class: 'tool-meta-item' }, node('span', { text: 'Ostatnio zastosowano' }), node('strong', { text: assignment.last_applied_at ? formatDate(assignment.last_applied_at) : '—' })),
        assignment.last_error ? node('p', { class: 'form-error wide', text: assignment.last_error }) : null)
      : node('p', { class: 'muted', text: 'Ta VM nie ma przypisanego Availability Planu.' }));

  const selector = selectField(
    'Availability Plan',
    'availability_plan_id',
    plans.map(plan => ({
      value: plan.id,
      label: `${plan.name} · ${stateLabel(plan.state)}`,
    })),
    assignment?.plan_id || plans[0]?.id || '',
    { required: true },
  );
  const form = node('form', { class: 'panel', autocomplete: 'off' },
    node('div', { class: 'panel-header' },
      node('div', {},
        node('h2', { text: 'Zmień plan' }),
        node('p', { class: 'muted', text: 'Zmiana tworzy kontrolowane zadanie Day-2 i aktualizuje wpis vm:<VMID> w Proxmox HA.' }))),
    plans.length
      ? node('div', { class: 'form-grid' }, selector)
      : node('div', { class: 'empty', text: 'Brak aktywnych Availability Planów w tym projekcie.' }),
    plans.length && scopeAllows('availability.assign')
      ? node('div', { class: 'form-actions' },
        button('Zastosuj plan', async () => {
          try {
            const planId = selector.querySelector('select').value;
            if (!planId) throw new Error('Wybierz Availability Plan.');
            const result = await api('/availability-plans/resources/' + encodeURIComponent(item.id), {
              method: 'PUT',
              body: { plan_id: planId },
              headers,
            });
            const queued = result.assignment?.status;
            toast(queued === 'waiting_approval'
              ? 'Zmiana AV oczekuje na akceptację.'
              : 'Zmiana Availability Planu została dodana do kolejki.');
            navigate('/resources/vm/' + encodeURIComponent(item.id) + '/availability');
          } catch (error) {
            toast(error.message, 'error');
          }
        }, 'primary'))
      : null);

  form.addEventListener('submit', event => event.preventDefault());
  return node('div', { class: 'vm-detail-stack' }, current, form);
}

async function canReadResource(item) {
  try {
    await api('/availability-plans/resources/' + encodeURIComponent(item.id), {
      headers: resourceHeaders(item),
    });
    return true;
  } catch (error) {
    if (error?.status === 403 || error?.status === 404) return false;
    throw error;
  }
}

window.AvailabilityPlans = Object.freeze({
  card,
  vmContent,
  canReadResource,
  scopeHeaders,
});

registerView({
  id: 'availability-plans',
  label: 'Availability Plan',
  iconName: 'shield',
  navigation: false,
  navigationParent: 'tools',
  permission: null,
  order: 159,
}, availabilityPlansView);
})();
