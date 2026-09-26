'use strict';

(() => {
  let capabilities = null;

  const statusLabels = {
    draft: 'Draft',
    dry_run: 'Dry-run',
    enforced: 'Enforced',
    disabled: 'Disabled',
    archived: 'Archived',
  };

  function jsonValue(value, label) {
    try {
      const parsed = JSON.parse(String(value || '').trim() || '{}');
      return parsed;
    } catch (error) {
      throw new Error(label + ': niepoprawny JSON (' + error.message + ').');
    }
  }

  function jsonObject(value, label) {
    const parsed = jsonValue(value, label);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(label + ': wymagany obiekt JSON.');
    }
    return parsed;
  }

  function jsonArray(value, label) {
    const parsed = jsonValue(value, label);
    if (!Array.isArray(parsed)) throw new Error(label + ': wymagana tablica JSON.');
    return parsed;
  }

  function pretty(value) {
    return JSON.stringify(value ?? {}, null, 2);
  }

  async function loadCapabilities() {
    if (!capabilities) capabilities = await api('/policies/capabilities');
    return capabilities;
  }

  function scopeLabel(item) {
    if (item.scope_level === 'global') return 'Global';
    if (item.scope_level === 'tenant') return 'Tenant';
    return 'Projekt';
  }

  function policyStatus(item) {
    const status = statusLabels[item.status] || item.status;
    const kind = item.status === 'enforced' ? 'ok'
      : item.status === 'dry_run' ? 'warning'
      : item.status === 'disabled' || item.status === 'archived' ? 'muted'
      : 'info';
    return badge(status, kind);
  }

  function policyEditor(item = null) {
    loadCapabilities().then(caps => {
      const defaultScope = item?.scope || {
        actions: ['vm.create'],
        resource_types: ['vm'],
        apmids: ['LEO'],
        environments: ['dev'],
      };
      const defaultCondition = item?.condition || {};
      const defaultEffects = item?.effects || [
        { type: 'allow', mode: 'whitelist', message: 'Dozwolony deployment w wybranym APMID/ENV' },
      ];
      const body = node('div', { class: 'policy-form form-grid' },
        field('Nazwa', 'name', { required: true, maxlength: 160, value: item?.name || '' }),
        selectField('Typ', 'policy_type',
          caps.policy_types.map(value => ({ value, label: value })),
          item?.policy_type || 'access'),
        selectField('Scope', 'scope_level', [
          { value: 'project', label: 'Projekt' },
          { value: 'tenant', label: 'Tenant' },
          { value: 'global', label: 'Global' },
        ], item?.scope_level || 'project'),
        selectField('Status', 'status', caps.statuses.map(value => ({
          value, label: statusLabels[value] || value,
        })), item?.status || 'draft'),
        selectField('Enforcement', 'enforcement', [
          { value: 'hard', label: 'HARD' },
          { value: 'soft', label: 'SOFT' },
          { value: 'advisory', label: 'ADVISORY' },
        ], item?.enforcement || 'hard'),
        field('Priorytet', 'priority', {
          type: 'number', min: 0, max: 10000, required: true,
          value: item?.priority ?? 5000,
        }),
        field('Opis', 'description', {
          tag: 'textarea', maxlength: 8000, value: item?.description || '', wide: true,
        }),
        field('Scope / selektory — JSON', 'scope', {
          tag: 'textarea', value: pretty(defaultScope), wide: true,
          help: 'Np. user_ids, roles, groups, actions, apmids, environments, blueprint_ids, tags.',
        }),
        field('Warunek — JSON', 'condition', {
          tag: 'textarea', value: pretty(defaultCondition), wide: true,
          help: 'Drzewo all/any/not lub leaf: {"field":"resource.cpu","operator":"lte","value":8}.',
        }),
        field('Efekty — JSON', 'effects', {
          tag: 'textarea', value: pretty(defaultEffects), wide: true,
          help: 'Np. allow/deny/require_approval/limit_value/force_value/set_default/add_tag/select_storage.',
        })
      );

      openModal({
        title: item ? 'Edytuj politykę' : 'Nowa polityka',
        eyebrow: 'Policy Engine',
        body,
        wide: true,
        submitLabel: item ? 'Zapisz nową wersję' : 'Utwórz politykę',
        onSubmit: async data => {
          const payload = {
            name: String(data.get('name') || '').trim(),
            description: String(data.get('description') || ''),
            policy_type: String(data.get('policy_type')),
            priority: Number(data.get('priority')),
            enforcement: String(data.get('enforcement')),
            status: String(data.get('status')),
            scope_level: String(data.get('scope_level')),
            scope: jsonObject(data.get('scope'), 'Scope'),
            condition: jsonObject(data.get('condition'), 'Warunek'),
            effects: jsonArray(data.get('effects'), 'Efekty'),
          };
          if (item) payload.expected_version = item.version;
          await api(item ? '/policies/' + encodeURIComponent(item.id) : '/policies', {
            method: item ? 'PUT' : 'POST',
            body: payload,
          });
          toast(item ? 'Polityka zapisana jako nowa wersja.' : 'Polityka utworzona.');
          await navigate('policies');
          return false;
        },
      });
    }).catch(error => toast(error.message, 'error'));
  }

  async function rollbackPolicy(item, version) {
    await api('/policies/' + encodeURIComponent(item.id) + '/rollback', {
      method: 'POST',
      body: { version: Number(version), expected_version: item.version },
    });
    toast('Przywrócono wersję polityki.');
    await navigate('policies');
  }

  async function versionsModal(item) {
    const rows = (await api('/policies/' + encodeURIComponent(item.id) + '/versions')).items || [];
    openModal({
      title: item.name + ' — wersje',
      eyebrow: 'Policy Engine / historia',
      wide: true,
      body: node('div', { class: 'stack' },
        table([
          { label: 'Wersja', value: row => '#' + row.version },
          { label: 'Utworzono', value: row => formatDate(row.created_at) },
          { label: 'Status', value: row => row.snapshot?.status || '—' },
          { label: 'Priorytet', value: row => row.snapshot?.priority ?? '—' },
        ], rows, row => row.version !== item.version && allowed('policies.manage')
          ? [button('Rollback', () => rollbackPolicy(item, row.version), 'ghost')]
          : []),
        node('details', { class: 'policy-json-details' },
          node('summary', { text: 'Pełne snapshoty JSON' }),
          node('pre', { class: 'policy-json', text: pretty(rows) }))
      ),
      submitLabel: null,
    });
  }

  function exceptionForm(item) {
    const body = node('div', { class: 'form-grid' },
      field('Nazwa wyjątku', 'name', { required: true, maxlength: 160 }),
      field('Ticket / Change', 'ticket', { maxlength: 160 }),
      field('Powód', 'reason', { tag: 'textarea', required: true, maxlength: 8000, wide: true }),
      selectField('Status', 'status', [
        { value: 'approved', label: 'Approved' },
        { value: 'pending', label: 'Pending' },
      ], 'approved'),
      field('Ważny od (ISO 8601)', 'valid_from', { placeholder: '2026-09-26T08:00:00Z' }),
      field('Ważny do (ISO 8601)', 'valid_until', { placeholder: '2026-09-27T08:00:00Z' }),
      field('Warunek wyjątku — JSON', 'condition', {
        tag: 'textarea', wide: true,
        value: pretty({ field: 'actor.id', operator: 'eq', value: state.identity?.user?.id || 0 }),
      })
    );
    openModal({
      title: 'Nowy wyjątek — ' + item.name,
      eyebrow: 'Policy Engine / exception',
      body,
      wide: true,
      submitLabel: 'Dodaj wyjątek',
      onSubmit: async data => {
        const payload = {
          name: String(data.get('name') || '').trim(),
          ticket: String(data.get('ticket') || '').trim(),
          reason: String(data.get('reason') || '').trim(),
          status: String(data.get('status')),
          condition: jsonObject(data.get('condition'), 'Warunek wyjątku'),
          valid_from: String(data.get('valid_from') || '').trim() || null,
          valid_until: String(data.get('valid_until') || '').trim() || null,
        };
        await api('/policies/' + encodeURIComponent(item.id) + '/exceptions', {
          method: 'POST', body: payload,
        });
        toast('Wyjątek zapisany.');
        await policyDetails(item.id);
        return false;
      },
    });
  }

  async function revokeException(item, exception) {
    await api('/policies/' + encodeURIComponent(item.id) + '/exceptions/' + encodeURIComponent(exception.id), {
      method: 'DELETE',
    });
    toast('Wyjątek wycofany.');
    await policyDetails(item.id);
  }

  async function policyDetails(id) {
    const item = await api('/policies/' + encodeURIComponent(id));
    const exceptions = (await api('/policies/' + encodeURIComponent(id) + '/exceptions')).items || [];
    const actions = [
      allowed('policies.manage') ? button('Edytuj', () => policyEditor(item), 'primary') : null,
      button('Wersje', () => versionsModal(item), 'ghost'),
      allowed('policies.exception.manage') ? button('Dodaj wyjątek', () => exceptionForm(item), 'ghost') : null,
    ].filter(Boolean);
    if (allowed('policies.manage') && item.status !== 'archived') {
      actions.push(button('Archiwizuj', async () => {
        await api('/policies/' + encodeURIComponent(item.id), { method: 'DELETE' });
        toast('Polityka zarchiwizowana.');
        await navigate('policies');
      }, 'danger'));
    }

    openModal({
      title: item.name,
      eyebrow: 'Policy Engine / szczegóły',
      wide: true,
      body: node('div', { class: 'stack' },
        node('div', { class: 'action-group' }, actions),
        node('div', { class: 'policy-meta-grid' },
          node('div', {}, node('span', { class: 'muted', text: 'Typ' }), node('strong', { text: item.policy_type })),
          node('div', {}, node('span', { class: 'muted', text: 'Scope' }), node('strong', { text: scopeLabel(item) })),
          node('div', {}, node('span', { class: 'muted', text: 'Status' }), policyStatus(item)),
          node('div', {}, node('span', { class: 'muted', text: 'Enforcement' }), node('strong', { text: item.enforcement.toUpperCase() })),
          node('div', {}, node('span', { class: 'muted', text: 'Priorytet' }), node('strong', { text: String(item.priority) })),
          node('div', {}, node('span', { class: 'muted', text: 'Wersja' }), node('strong', { text: '#' + item.version }))
        ),
        node('p', { text: item.description || 'Brak opisu.' }),
        node('h3', { text: 'Definicja' }),
        node('pre', { class: 'policy-json', text: pretty({
          scope: item.scope,
          condition: item.condition,
          effects: item.effects,
        }) }),
        node('h3', { text: 'Wyjątki' }),
        exceptions.length
          ? table([
              { label: 'Nazwa', value: row => row.name },
              { label: 'Status', value: row => row.status },
              { label: 'Ticket', value: row => row.ticket || '—' },
              { label: 'Ważny do', value: row => row.valid_until ? formatDate(row.valid_until) : 'bez limitu' },
            ], exceptions, row => allowed('policies.exception.manage') && row.status !== 'revoked'
              ? [button('Wycofaj', () => revokeException(item, row), 'ghost')]
              : [])
          : node('p', { class: 'muted', text: 'Brak wyjątków.' })
      ),
      submitLabel: null,
    });
  }

  function simulator() {
    const body = node('div', { class: 'form-grid' },
      field('Action', 'action', { required: true, value: 'vm.create' }),
      field('Resource type', 'resource_type', { required: true, value: 'vm' }),
      field('Resource ID', 'resource_id', { value: '' }),
      field('Context — JSON', 'context', {
        tag: 'textarea', wide: true,
        value: pretty({
          resource: {
            apmid: 'LEO',
            environment: 'dev',
            cpu: 4,
            memory_mb: 8192,
            tags: ['linux'],
          },
          blueprint: { id: 1, slug: 'ubuntu-server' },
        }),
      }),
      node('div', { class: 'wide policy-simulator-result', hidden: true })
    );
    const output = body.querySelector('.policy-simulator-result');
    openModal({
      title: 'Symulator Policy Engine',
      eyebrow: 'Explain / dry-run',
      body,
      wide: true,
      submitLabel: 'Oceń',
      onSubmit: async data => {
        const result = await api('/policies/evaluate', {
          method: 'POST',
          body: {
            action: String(data.get('action')),
            resource_type: String(data.get('resource_type')),
            resource_id: String(data.get('resource_id') || '').trim() || null,
            context: jsonObject(data.get('context'), 'Context'),
          },
        });
        output.hidden = false;
        output.replaceChildren(
          node('div', { class: 'policy-decision policy-decision-' + result.decision },
            node('strong', { text: 'DECISION: ' + String(result.decision).toUpperCase() })),
          node('pre', { class: 'policy-json', text: pretty(result) })
        );
        return false;
      },
    });
  }

  async function decisionLog() {
    const rows = (await api('/policies/decisions?limit=200&offset=0')).items || [];
    openModal({
      title: 'Decision Log',
      eyebrow: 'Policy Engine / audit',
      wide: true,
      body: node('div', { class: 'stack' },
        table([
          { label: 'Czas', value: row => formatDate(row.timestamp) },
          { label: 'Action', value: row => row.action },
          { label: 'Resource', value: row => row.resource_type + (row.resource_id ? ' / ' + row.resource_id : '') },
          { label: 'Decision', value: row => badge(row.decision, row.decision === 'allow' ? 'ok' : row.decision === 'deny' ? 'danger' : 'warning') },
          { label: 'Policies', value: row => (row.matched_policy_ids || []).length },
        ], rows, row => [button('Trace', () => openModal({
          title: 'Decision ' + row.id,
          eyebrow: row.action,
          wide: true,
          body: node('pre', { class: 'policy-json', text: pretty(row) }),
          submitLabel: null,
        }), 'ghost')])
      ),
      submitLabel: null,
    });
  }

  async function policiesView() {
    await loadCapabilities();
    const rows = (await api('/policies')).items || [];
    const actions = [];
    if (allowed('policies.manage')) actions.push(button('Nowa polityka', () => policyEditor(), 'primary'));
    if (allowed('policies.simulate')) actions.push(button('Symulator', simulator, 'ghost'));
    if (allowed('policies.audit')) actions.push(button('Decision Log', decisionLog, 'ghost'));

    dom.content.replaceChildren(
      heading('Policy Engine', actions),
      node('p', {
        class: 'muted',
        text: 'Centralne reguły ABAC/policy dla Blueprintów, APMID/ENV, Day-2, approval, limitów, placement i lifecycle. RBAC nadal określa bazowe uprawnienie do akcji.',
      }),
      table([
        { label: 'Nazwa', value: item => node('strong', { text: item.name }) },
        { label: 'Typ', value: item => item.policy_type },
        { label: 'Scope', value: item => scopeLabel(item) },
        { label: 'Status', value: item => policyStatus(item) },
        { label: 'Tryb', value: item => item.enforcement.toUpperCase() },
        { label: 'Priorytet', value: item => item.priority },
        { label: 'Wersja', value: item => '#' + item.version },
        { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
      ], rows, item => [button('Szczegóły', () => policyDetails(item.id), 'ghost')])
    );
  }

  registerCommand('policies.create', () => policyEditor());
  registerView({
    id: 'policies',
    label: 'Policy Engine',
    icon: 'P',
    permission: 'policies.read',
    order: 25,
  }, policiesView);
})();
