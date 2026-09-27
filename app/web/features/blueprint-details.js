'use strict';

(() => {
  function scopeHeaders(scope) {
    return {
      'X-Tenant-ID': String(scope.tenant_id),
      'X-Project-ID': String(scope.project_id),
    };
  }

  function scopeAllows(scope, permission) {
    return allowed(permission) || (scope?.permissions || []).includes(permission);
  }

  function preferredScopes(scopes, tenantId = '', projectId = '') {
    const exact = scopes.find(scope =>
      String(scope.tenant_id) === String(tenantId || '')
      && String(scope.project_id) === String(projectId || '')
    );
    return exact ? [exact, ...scopes.filter(scope => scope !== exact)] : scopes;
  }

  async function resolveBlueprintDetails(id, searchParams) {
    const result = await api('/blueprints/creation-scopes?permission=blueprints.read&limit=200');
    const scopes = preferredScopes(
      result.items || [],
      searchParams.get('tenantId') || '',
      searchParams.get('projectId') || ''
    );
    if (!scopes.length) throw new Error('Brak projektu z uprawnieniem blueprints.read.');

    for (const scope of scopes) {
      try {
        const item = await api('/blueprints/' + encodeURIComponent(id), { headers: scopeHeaders(scope) });
        return { item, scope };
      } catch (error) {
        if (![403, 404].includes(Number(error?.status))) throw error;
      }
    }
    throw new Error('Blueprint nie jest dostępny w projektach, w których masz uprawnienie blueprints.read.');
  }

  function detailsLink(label, path, kind = '') {
    return node('a', {
      class: ('button blueprint-details-link ' + kind).trim(),
      href: '#' + path,
      text: label,
    });
  }

  function valueText(value) {
    if (value === null || value === undefined || value === '') return '—';
    if (value === true) return 'Tak';
    if (value === false) return 'Nie';
    if (Array.isArray(value)) return value.length ? value.join(', ') : '—';
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }

  function fact(label, value, options = {}) {
    const rendered = options.node || node(options.mono ? 'code' : 'strong', {
      class: options.mono ? 'mono' : '',
      text: valueText(value),
    });
    return node('div', { class: 'blueprint-details-fact' },
      node('span', { text: label }),
      rendered);
  }

  function metric(label, value, detail) {
    return node('article', { class: 'blueprint-details-metric' },
      node('span', { text: label }),
      node('strong', { text: valueText(value) }),
      detail ? node('small', { class: 'muted', text: detail }) : null);
  }

  function section(title, description, ...children) {
    return node('section', { class: 'panel blueprint-details-section' },
      node('div', { class: 'blueprint-details-section-head' },
        node('div', {},
          node('h2', { text: title }),
          description ? node('p', { class: 'muted', text: description }) : null)),
      ...children);
  }

  function chipList(values, emptyText = 'Brak') {
    const items = (values || []).filter(value => value !== null && value !== undefined && value !== '');
    if (!items.length) return node('span', { class: 'muted', text: emptyText });
    return node('div', { class: 'blueprint-details-chips' },
      ...items.map(value => node('span', { class: 'blueprint-details-chip', text: String(value) })));
  }

  function roleLabels(ids, roleNames) {
    return (ids || []).map(id => roleNames.get(Number(id)) || ('Rola #' + id));
  }

  function visibilityBadges(visibility = {}) {
    const labels = { backend: 'Backend', cloudportal: 'CloudPortal', api: 'API' };
    const enabled = Object.entries(visibility)
      .filter(([, value]) => Boolean(value))
      .map(([key]) => labels[key] || key);
    return chipList(enabled, 'Nigdzie');
  }

  function workflowTable(item) {
    const rows = (item.workflow || []).map((step, index) => ({
      ...step,
      order: index + 1,
      depends: (step.depends_on || []).join(', ') || '—',
    }));
    if (!rows.length) return node('div', { class: 'empty-state' },
      node('strong', { text: 'Brak kroków workflow' }),
      node('p', { class: 'muted', text: 'Blueprint nie zawiera żadnych kroków wykonawczych.' }));

    return table([
      { label: '#', value: row => row.order },
      { label: 'ID', value: row => node('span', { class: 'mono', text: row.id || '—' }) },
      { label: 'Typ', value: row => badge(row.type || '—', 'info') },
      { label: 'Po', value: row => node('span', { class: 'mono', text: row.depends }) },
      { label: 'Retry', value: row => row.retry ?? '—' },
      { label: 'Timeout', value: row => row.timeout ? row.timeout + ' s' : '—' },
    ], rows);
  }

  function variableSchemaTable(item) {
    const rows = Object.entries(item.variables_schema || {}).map(([name, definition]) => ({
      name,
      ...definition,
    }));
    if (!rows.length) return node('p', { class: 'muted', text: 'Blueprint nie wymaga parametrów wejściowych od użytkownika.' });

    return table([
      { label: 'Pole', value: row => node('div', {},
        node('strong', { text: row.label || row.name }),
        node('div', { class: 'mono muted', text: row.name })) },
      { label: 'Typ', value: row => row.type || 'text' },
      { label: 'Wymagane', value: row => row.required ? badge('Tak', 'warning') : 'Nie' },
      { label: 'Domyślna', value: row => valueText(row.default) },
      { label: 'Ograniczenia', value: row => {
        const parts = [];
        if (Array.isArray(row.options) && row.options.length) parts.push('Opcje: ' + row.options.join(', '));
        if (row.min !== undefined) parts.push('min ' + row.min);
        if (row.max !== undefined) parts.push('max ' + row.max);
        return parts.join(' · ') || '—';
      } },
    ], rows);
  }

  function deploymentVariablesTable(item) {
    const rows = Object.entries(item.deployment?.variables || {}).map(([name, value]) => ({ name, value }));
    if (!rows.length) return node('p', { class: 'muted', text: 'Brak dodatkowych parametrów provisioning.' });

    return table([
      { label: 'Parametr', value: row => node('span', { class: 'mono', text: row.name }) },
      { label: 'Wartość', value: row => node('span', {
        class: typeof row.value === 'string' && row.value.includes('{{') ? 'mono' : '',
        text: valueText(row.value),
      }) },
    ], rows);
  }

  async function blueprintDetails(id, searchParams = new URLSearchParams()) {
    const resolved = await resolveBlueprintDetails(id, searchParams);
    const { item, scope } = resolved;
    const headers = scopeHeaders(scope);
    const canManage = window.BlueprintsFeature?.canManage?.(item) !== false;

    const [rolesResult, avatarResult, providersResult, hostnameResult] = await Promise.all([
      allowed('roles.read') ? api('/roles?limit=200').catch(() => ({ items: [] })) : Promise.resolve({ items: [] }),
      api('/blueprint-avatars').catch(() => ({ items: [] })),
      scopeAllows(scope, 'providers.read')
        ? api('/providers?limit=200', { headers }).catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] }),
      scopeAllows(scope, 'hostnames.read')
        ? api('/hostname-schemes?limit=200', { headers }).catch(() => ({ items: [] }))
        : Promise.resolve({ items: [] }),
    ]);

    const roleNames = new Map((rolesResult.items || []).map(role => [Number(role.id), role.name]));
    const avatar = (avatarResult.items || []).find(value => String(value.id) === String(item.avatar_id || '')) || null;
    const provider = (providersResult.items || []).find(value => Number(value.id) === Number(item.deployment?.provider_id)) || null;
    const hostnameScheme = (hostnameResult.items || []).find(value => Number(value.id) === Number(item.deployment?.hostname_scheme_id)) || null;

    const actions = [detailsLink('← Blueprinty', 'blueprints')];
    const executionControl = window.BlueprintProvisioningGuards?.executionControl?.(
      item,
      () => navigate(window.BlueprintsFeature.executionPath(item, scope)),
      permission => scopeAllows(scope, permission)
    );
    if (executionControl) actions.push(executionControl);

    if (scopeAllows(scope, 'blueprints.update') && canManage) {
      actions.push(button('Edytuj', () => window.BlueprintWizard.open({
        item,
        tenantId: scope.tenant_id,
        projectId: scope.project_id,
      }), 'primary'));
      if (window.BlueprintVRADesigner && allowed('blueprints.update')) {
        actions.push(button('Designer vRA / YAML', () => window.BlueprintVRADesigner.open(item)));
      }
    }

    const deployment = item.deployment || {};
    const ansibleRuns = Array.isArray(deployment.ansible_runs)
      ? deployment.ansible_runs
      : (deployment.ansible ? [deployment.ansible] : []);

    dom.pageEyebrow.textContent = 'Blueprinty / Szczegóły';
    dom.pageTitle.textContent = item.name;

    dom.content.replaceChildren(
      node('div', { class: 'blueprint-details-page' },
        node('div', { class: 'blueprint-details-toolbar' }, ...actions),

        node('section', { class: 'panel blueprint-details-hero' },
          node('div', { class: 'blueprint-details-avatar', 'aria-hidden': 'true' },
            avatar?.data_uri
              ? node('img', { src: avatar.data_uri, alt: '', loading: 'lazy', decoding: 'async' })
              : appIcon('box')),
          node('div', { class: 'blueprint-details-hero-copy' },
            node('div', { class: 'blueprint-details-title-row' },
              node('div', {},
                node('h1', { text: item.name }),
                node('div', { class: 'mono muted', text: item.slug + ' · v' + item.version })),
              badge(statusLabel(item.is_active ? 'active' : 'inactive'), item.is_active ? 'ok' : 'danger')),
            node('p', {
              class: item.description ? '' : 'muted',
              text: item.description || 'Brak opisu Blueprintu.',
            }),
            node('div', { class: 'blueprint-details-hero-meta' },
              node('span', { text: (scope.tenant_name || scope.tenant_id) + ' / ' + (scope.project_name || scope.project_id) }),
              node('span', { text: 'Aktualizacja: ' + formatDate(item.updated_at) }))));

        node('div', { class: 'blueprint-details-metrics' },
          metric('Wersja', 'v' + item.version, 'wersja definicji'),
          metric('Workflow', (item.workflow || []).length, 'liczba kroków'),
          metric('Parametry wejściowe', Object.keys(item.variables_schema || {}).length, 'pola formularza'),
          metric('Provisioning', deployment.executor || deployment.template || '—', provider?.name || 'platforma')),

        section('Podsumowanie', 'Najważniejsze informacje o definicji i miejscu jej użycia.',
          node('div', { class: 'blueprint-details-facts' },
            fact('Organizacja', scope.tenant_name || scope.tenant_id),
            fact('Projekt', scope.project_name || scope.project_id),
            fact('ID Blueprintu', item.id, { mono: true }),
            fact('Slug', item.slug, { mono: true }),
            fact('Utworzono', formatDate(item.created_at)),
            fact('Ostatnia aktualizacja', formatDate(item.updated_at)),
            fact('Utworzył użytkownik', item.created_by ? '#' + item.created_by : '—'),
            fact('Avatar', item.avatar_id || 'Domyślny'))),

        section('Dostęp i RBAC', 'Widoczność Blueprintu, role zarządzające oraz ograniczenia self-service.',
          node('div', { class: 'blueprint-details-facts' },
            fact('Widoczność', '', { node: visibilityBadges(item.visibility) }),
            fact('Role zarządzające', '', { node: chipList(roleLabels(item.manager_role_ids, roleNames), 'Bez dedykowanej roli') }),
            fact('Dozwolone role', '', { node: chipList(roleLabels(item.allowed_role_ids, roleNames), 'Wszystkie role w dozwolonym scope') }),
            fact('Dozwoleni użytkownicy', '', { node: chipList((item.allowed_user_ids || []).map(value => 'Użytkownik #' + value), 'Bez ograniczenia do użytkowników') }))),

        section('Zasady wykonania', 'Approval i zachowanie zasobów po błędzie.',
          node('div', { class: 'blueprint-details-facts' },
            fact('Wymaga akceptacji', item.requires_approval),
            fact('Auto-approval wykonawcy', item.auto_approve_for_executors === null || item.auto_approve_for_executors === undefined
              ? 'Dziedziczone'
              : item.auto_approve_for_executors),
            fact('Timeout akceptacji', item.approval_timeout_hours ? item.approval_timeout_hours + ' h' : 'Dziedziczony'),
            fact('Recovery', item.recovery_policy === 'destroy_on_failure' ? 'Usuń zasoby po błędzie' : 'Zachowaj zasoby po błędzie'))),

        section('Provisioning', 'Źródło infrastruktury i ustawienia zapisane w definicji deploymentu.',
          node('div', { class: 'blueprint-details-facts' },
            fact('Template', deployment.template || '—', { mono: true }),
            fact('Platforma', provider?.name || (deployment.provider_id ? '#' + deployment.provider_id : '—')),
            fact('Credential platformy', deployment.credentials_id ? '#' + deployment.credentials_id : '—'),
            fact('Nazwa zasobu', deployment.name || '—', { mono: true }),
            fact('Executor', deployment.executor || 'Domyślny'),
            fact('Environment', deployment.environment || (deployment.select_environment_on_execute ? 'Wybierane przy uruchomieniu' : '—')),
            fact('APMID', deployment.apmid || (deployment.select_apmid_on_execute ? 'Wybierane przy uruchomieniu' : '—')),
            fact('Schemat hostname', hostnameScheme
              ? hostnameScheme.name + ' · ' + hostnameScheme.pattern
              : (deployment.hostname_scheme_id ? '#' + deployment.hostname_scheme_id : '—')),
            fact('Pula IPAM', deployment.ipam_pool_id ? '#' + deployment.ipam_pool_id : '—'),
            fact('Credential systemu gościa', deployment.guest_credential_id ? '#' + deployment.guest_credential_id : '—'),
            fact('Runbooki Ansible', ansibleRuns.length),
            fact('AWX / Controller', deployment.awx ? 'Włączony' : 'Wyłączony')),
          node('div', { class: 'blueprint-details-subsection' },
            node('h3', { text: 'Parametry provisioning' }),
            deploymentVariablesTable(item))),

        section('Workflow', 'Kolejność wykonania kroków oraz zależności DAG.',
          workflowTable(item)),

        section('Formularz uruchomienia', 'Pola, które użytkownik może lub musi podać podczas tworzenia zasobu z Blueprintu.',
          variableSchemaTable(item))
      )
    );
  }

  registerRoutedForm({
    id: 'blueprints-details',
    pattern: /^\/blueprints\/(?<id>\d+)\/(?<slug>(?!execute$)[^/]+)$/,
    parent: 'blueprints',
    permission: null,
    label: 'Blueprinty',
  }, match => blueprintDetails(Number(match.params.id), match.searchParams));

  registerExtension('blueprint-details', () => {});
})();
