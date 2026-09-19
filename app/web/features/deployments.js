'use strict';

(() => {
async function deploymentsView() {
  const [deploymentResult, providerResult] = await Promise.all([
    api('/deployments?limit=200'),
    allowed('providers.read') ? api('/providers?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const deployments = deploymentResult.items;
  const providerNames = new Map(providerResult.items.map(provider => [Number(provider.id), provider.name]));
  const actions = allowed('deployments.create') && allowed('providers.read') && allowed('credentials.read') && allowed('terraform.read')
    ? [button('Nowe wdrożenie', createDeployment, 'primary')] : [];
  dom.content.replaceChildren(heading('Kontrolowane wdrożenia Terraform/OpenTofu. Każda operacja tworzy audytowalne zadanie.', actions),
    table([
      { label: 'Nazwa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: short(item.id, 18) })) },
      { label: 'Platforma', value: item => providerNames.get(Number(item.provider_id)) || CREDENTIAL_TYPE_CONFIG[item.provider]?.label || `#${item.provider_id}` }, { label: 'Silnik IaC', value: item => badge(item.executor, 'info') },
      { label: 'Status', value: item => badge(statusLabel(item.status), statusKind(item.status)) }, { label: 'Aktualizacja', value: item => formatDate(item.updated_at) },
    ], deployments, item => deploymentActions(item)));
}

function deploymentActions(item) {
  const actions = [button('Szczegóły', () => showDeploymentDetails(item))];
  if (allowed('jobs.execute') && allowed('terraform.execute') && !item.active_job_id && item.status !== 'destroyed') {
    actions.push(button('Plan', () => createTerraformJob(item, 'terraform.plan')));
    actions.push(button('Zastosuj', () => createTerraformJob(item, 'terraform.apply')));
  }
  if (allowed('deployments.destroy') && allowed('jobs.execute') && !item.active_job_id && item.status !== 'destroyed') actions.push(button('Usuń zasoby', () => confirmAction('Usuń zasoby wdrożenia', `Terraform usunie zasoby wdrożenia ${item.name}.`, async () => { await api(`/deployments/${item.id}/destroy`, { method: 'POST', body: {}, idempotent: true }); toast('Utworzono zadanie usuwania zasobów.'); navigate('deployments'); }), 'danger'));
  return actions;
}

function showDeploymentDetails(item) {
  const variableRows = Object.entries(item.variables || {}).map(([key, value]) => ({ key, value }));
  const workflow = item.workflow || {};
  const overview = node('div', { class: 'checks' },
    info('Status', statusLabel(item.status)),
    info('Platforma', CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider),
    info('Szablon', item.template),
    info('Silnik IaC', item.executor),
    info('Utworzono', formatDate(item.created_at)),
    info('Aktualizacja', formatDate(item.updated_at)));
  const content = node('div', { class: 'stack' },
    overview,
    node('section', { class: 'detail-section' },
      node('h3', { text: 'Parametry wdrożenia' }),
      variableRows.length ? table([
        { label: 'Pole', value: row => FIELD_LABELS[row.key] || row.key.replaceAll('_', ' ') },
        { label: 'Wartość', value: row => displayValue(row.value) },
      ], variableRows) : node('p', { class: 'muted', text: 'Brak parametrów.' })));
  if (workflow.ansible) {
    content.append(node('section', { class: 'detail-section' },
      node('h3', { text: 'Konfiguracja Ansible' }),
      node('div', { class: 'checks' },
        info('Playbook', workflow.ansible.playbook),
        info('Dane dostępowe', `#${workflow.ansible.credentials_id}`))));
  }
  dom.modal.classList.add('modal-wide');
  dom.modalTitle.textContent = item.name;
  dom.modalEyebrow.textContent = `Wdrożenie · ${short(item.id, 18)}`;
  dom.modalBody.replaceChildren(content);
  const actions = [button('Zamknij', closeModal)];
  if (item.active_job_id && allowed('jobs.read')) actions.unshift(button('Przejdź do zadań', () => { closeModal(); navigate('jobs'); }, 'primary'));
  dom.modalActions.replaceChildren(...actions);
  if (!dom.modal.open) dom.modal.showModal();
}

async function createTerraformJob(item, operation) {
  try { await api('/jobs', { method: 'POST', body: { operation, deployment_id: item.id }, idempotent: true }); toast(`Utworzono zadanie: ${operationLabel(operation)}.`); navigate('jobs'); }
  catch (error) { toast(error.message, 'error'); }
}

async function createDeployment() {
  try {
    const [providerResult, credentialResult, templateResult, playbookResult] = await Promise.all([
      api('/providers?limit=200'),
      api('/credentials?limit=200'),
      api('/templates'),
      allowed('ansible.execute') && allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
    ]);
    const providers = providerResult.items;
    const credentials = credentialResult.items;
    const templates = templateResult.items;
    const playbooks = playbookResult.items;
    if (!providers.length) throw new Error('Najpierw dodaj provider infrastruktury.');
    if (!credentials.length) throw new Error('Najpierw dodaj credential infrastruktury.');
    if (!templates.length) throw new Error('Katalog nie zawiera żadnego szablonu wdrożenia.');

    const templateField = selectField('Szablon', 'template', templates.map(item => ({
      value: item.id, label: `${item.name} · v${item.version} · ${CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider}`,
    })), templates[0].id, { required: true });
    const providerField = selectField('Platforma', 'provider_id', [], '', { required: true });
    const credentialField = selectField('Dane dostępowe', 'credentials_id', [], '', { required: true });
    const variableFields = node('div', { class: 'form-grid wide template-variable-grid' });
    const ansibleFields = node('div', { class: 'form-grid wide ansible-fields' });
    const ansibleToggle = checkboxField('Po utworzeniu skonfiguruj system przez Ansible', 'ansible_enabled', false);
    const ansibleSection = formSection('Konfiguracja po wdrożeniu', 'Opcjonalny, zatwierdzony playbook uruchamiany po uzyskaniu adresu VM.', ansibleToggle, ansibleFields);

    const fields = node('div', { class: 'form-grid' },
      formSection('Podstawowe informacje', 'Wybierz szablon i miejsce wdrożenia.',
        node('div', { class: 'form-grid' },
          field('Nazwa wdrożenia', 'name', { required: true, placeholder: 'np. web-prod-01' }),
          templateField,
          providerField,
          credentialField,
          selectField('Silnik IaC', 'executor', [{ value: 'terraform', label: 'Terraform' }, { value: 'opentofu', label: 'OpenTofu' }], 'terraform'))),
      formSection('Konfiguracja zasobu', 'Pola są generowane automatycznie ze schematu wybranego szablonu.', variableFields),
      ansibleSection);

    const templateSelect = templateField.querySelector('select');
    const providerSelect = providerField.querySelector('select');
    const credentialSelect = credentialField.querySelector('select');

    const currentTemplate = () => templates.find(item => item.id === templateSelect.value);
    const refill = (select, rows, placeholder) => {
      const previous = select.value;
      select.replaceChildren(node('option', { value: '', text: placeholder }));
      rows.forEach(row => select.append(node('option', { value: row.id, text: row.label, selected: String(row.id) === String(previous) })));
      if (!select.value && rows.length === 1) select.value = String(rows[0].id);
    };

    const refreshCredentials = () => {
      const provider = providers.find(item => String(item.id) === String(providerSelect.value));
      const matching = provider
        ? credentials.filter(item => Number(item.id) === Number(provider.credentials_id)).map(item => ({ id: item.id, label: item.name }))
        : [];
      refill(credentialSelect, matching, matching.length ? 'Dane dostępowe platformy' : 'Wybierz provider');
    };

    const renderAnsible = () => {
      const template = currentTemplate();
      const supported = template?.provider === 'proxmox' && playbooks.length > 0;
      ansibleSection.hidden = !supported;
      if (!supported) {
        ansibleToggle.querySelector('input').checked = false;
        ansibleFields.replaceChildren();
        return;
      }
      const enabled = ansibleToggle.querySelector('input').checked;
      const playbookField = selectField('Playbook', 'ansible_playbook', playbooks.map(playbook => ({
        value: playbook.id,
        label: `${playbook.name} · v${playbook.version}`,
      })), playbooks[0]?.id || '', { required: enabled });
      const credentialField = selectField('Systemowe dane dostępowe', 'ansible_credentials_id', [], '', { required: enabled });
      const variablesContainer = node('div', { class: 'form-grid wide' });
      ansibleFields.replaceChildren(playbookField, credentialField, variablesContainer);
      ansibleFields.querySelectorAll('input,select,textarea').forEach(control => { control.disabled = !enabled; });

      const refreshPlaybook = () => {
        const playbook = playbooks.find(value => value.id === playbookField.querySelector('select').value) || playbooks[0];
        const matchingCredentials = credentials.filter(value => value.type === playbook?.transport);
        const select = credentialField.querySelector('select');
        refill(select, matchingCredentials.map(value => ({ id: value.id, label: `${value.name} · ${credentialTypeLabel(value.type)}` })),
          matchingCredentials.length ? 'Wybierz systemowe dane dostępowe' : `Brak danych dostępowych typu ${playbook?.transport || ''}`);
        variablesContainer.replaceChildren();
        (playbook?.variables || []).forEach(name => variablesContainer.append(
          field(FIELD_LABELS[name] || name.replaceAll('_', ' '), `ansible_var_${name}`, {
            help: 'Opcjonalna zmienna zatwierdzonego playbooka.',
          })));
      };
      playbookField.querySelector('select').addEventListener('change', refreshPlaybook);
      refreshPlaybook();
    };

    const refreshTemplate = () => {
      const template = currentTemplate();
      if (!template) return;
      const matchingProviders = providers.filter(item => item.type === template.provider).map(item => ({ id: item.id, label: item.name }));
      refill(providerSelect, matchingProviders, matchingProviders.length ? 'Wybierz provider' : 'Brak połączenia z tą platformą');
      refreshCredentials();
      renderTemplateVariables(variableFields, template);
      renderAnsible();
    };

    templateSelect.addEventListener('change', refreshTemplate);
    providerSelect.addEventListener('change', refreshCredentials);
    ansibleToggle.querySelector('input').addEventListener('change', renderAnsible);
    refreshTemplate();

    openModal({
      title: 'Nowe wdrożenie',
      eyebrow: 'Terraform / OpenTofu',
      body: fields,
      submitLabel: 'Utwórz i uruchom',
      wide: true,
      onSubmit: async (_data, form) => {
        const template = currentTemplate();
        if (!providerSelect.value) throw new Error('Brak providera zgodnego z wybranym szablonem.');
        if (!credentialSelect.value) throw new Error('Brak credentiala zgodnego z wybraną platformą.');
        const body = {
          name: form.elements.name.value,
          provider_id: Number(providerSelect.value),
          template: template.id,
          credentials_id: Number(credentialSelect.value),
          executor: form.elements.executor.value,
          variables: readTemplateVariables(form, template),
        };
        if (form.elements.ansible_enabled?.checked) {
          const playbook = playbooks.find(value => value.id === form.elements.ansible_playbook?.value);
          if (!playbook) throw new Error('Wybierz playbook Ansible.');
          if (!form.elements.ansible_credentials_id?.value) throw new Error('Wybierz systemowe dane dostępowe dla Ansible.');
          const variables = {};
          (playbook.variables || []).forEach(name => {
            const value = form.elements[`ansible_var_${name}`]?.value?.trim();
            if (value) variables[name] = value;
          });
          body.ansible = {
            playbook: playbook.id,
            credentials_id: Number(form.elements.ansible_credentials_id.value),
            variables,
          };
        }
        await api('/deployments', { method: 'POST', idempotent: true, body });
        toast('Wdrożenie zostało utworzone i uruchomiono zastosowanie konfiguracji.');
        navigate('deployments');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

async function jobsView() {
  const jobs = (await api('/jobs?limit=200')).items;
  const actions = [];
  if (allowed('jobs.execute') && allowed('ansible.execute') && allowed('ansible.read') && allowed('credentials.read')) {
    actions.push(button('Uruchom Ansible', runStandaloneAnsible, 'primary'));
  }
  dom.content.replaceChildren(heading('Historia i bieżący stan wykonania. Logi są redagowane po stronie backendu.', actions),
    table([
      { label: 'ID', class: 'mono', value: item => short(item.id, 18) }, { label: 'Operacja', value: item => operationLabel(item.operation) },
      { label: 'Status', value: item => badge(statusLabel(item.status), statusKind(item.status)) }, { label: 'Źródło', value: item => item.source }, { label: 'Wdrożenie', class: 'mono', value: item => short(item.deployment_id, 14) },
      { label: 'Utworzono', value: item => formatDate(item.created_at) }, { label: 'Błąd', value: item => node('span', { class: item.error ? 'form-error' : 'muted', text: item.error || '—' }) },
    ], jobs, item => {
      const actions = [button('Logi', () => showJobLogs(item))];
      if (allowed('jobs.cancel') && ['queued', 'running'].includes(item.status)) actions.push(button('Anuluj', () => confirmAction('Anuluj zadanie', `Zadanie ${short(item.id)} otrzyma żądanie anulowania.`, async () => { await api(`/jobs/${item.id}/cancel`, { method: 'POST' }); toast('Zadanie anulowane.'); navigate('jobs'); }), 'danger'));
      if (allowed('jobs.execute') && ['failed', 'cancelled'].includes(item.status)) actions.push(button('Ponów', async () => {
        await api(`/jobs/${item.id}/retry`, { method: 'POST', idempotent: true });
        toast('Utworzono ponowienie zadania.');
        navigate('jobs');
      }));
      return actions;
    }));
}

async function runStandaloneAnsible() {
  try {
    const [playbookResult, credentialResult] = await Promise.all([
      api('/ansible/playbooks'),
      api('/credentials?limit=200'),
    ]);
    const playbooks = playbookResult.items;
    const credentials = credentialResult.items;
    if (!playbooks.length) throw new Error('Katalog nie zawiera playbooków Ansible.');

    const playbookField = selectField('Playbook', 'playbook', playbooks.map(playbook => ({
      value: playbook.id,
      label: `${playbook.name} · v${playbook.version}`,
    })), playbooks[0].id, { required: true });
    const credentialField = selectField('Dane dostępowe hosta', 'credentials_id', [], '', { required: true });
    const variables = node('div', { class: 'form-grid wide' });
    const fields = node('div', { class: 'form-grid' },
      formSection('Cel', 'Podaj adresy IP hostów, na których ma zostać uruchomiony zatwierdzony playbook.',
        node('div', { class: 'form-grid' },
          playbookField,
          credentialField,
          field('Adresy IP hostów', 'hosts', {
            tag: 'textarea',
            required: true,
            wide: true,
            placeholder: '10.0.0.10\n10.0.0.11',
            help: 'Jeden adres IPv4/IPv6 w wierszu lub adresy oddzielone przecinkami.',
          }))),
      formSection('Zmienne playbooka', 'Puste pola opcjonalne nie są wysyłane.', variables));

    const refresh = () => {
      const playbook = playbooks.find(value => value.id === playbookField.querySelector('select').value) || playbooks[0];
      const matching = credentials.filter(value => value.type === playbook.transport);
      const select = credentialField.querySelector('select');
      select.replaceChildren(node('option', { value: '', text: matching.length ? 'Wybierz dane dostępowe' : `Brak danych typu ${playbook.transport}` }));
      matching.forEach(value => select.append(node('option', { value: value.id, text: value.name })));
      if (matching.length === 1) select.value = String(matching[0].id);
      variables.replaceChildren();
      (playbook.variables || []).forEach(name => variables.append(field(
        FIELD_LABELS[name] || name.replaceAll('_', ' '),
        `ansible_job_var_${name}`,
        { help: 'Zmienna zatwierdzonego playbooka.' },
      )));
    };
    playbookField.querySelector('select').addEventListener('change', refresh);
    refresh();

    openModal({
      title: 'Uruchom Ansible',
      eyebrow: 'Zadanie jednorazowe',
      body: fields,
      submitLabel: 'Dodaj do kolejki',
      wide: true,
      onSubmit: async (_data, form) => {
        const playbook = playbooks.find(value => value.id === form.elements.playbook.value);
        if (!playbook) throw new Error('Wybierz playbook.');
        if (!form.elements.credentials_id.value) throw new Error('Wybierz dane dostępowe do hosta.');
        const hosts = splitValues(form.elements.hosts.value);
        if (!hosts.length) throw new Error('Podaj co najmniej jeden adres IP hosta.');
        const jobVariables = {};
        (playbook.variables || []).forEach(name => {
          const value = form.elements[`ansible_job_var_${name}`]?.value?.trim();
          if (value) jobVariables[name] = value;
        });
        await api('/jobs', {
          method: 'POST',
          idempotent: true,
          body: {
            operation: 'ansible.execute',
            ansible: {
              playbook: playbook.id,
              credentials_id: Number(form.elements.credentials_id.value),
              inventory: { hosts },
              variables: jobVariables,
            },
          },
        });
        toast('Zadanie Ansible zostało dodane do kolejki.');
        navigate('jobs');
      },
    });
  } catch (error) { toast(error.message, 'error'); }
}

async function showJobLogs(job) {
  dom.modalTitle.textContent = `Logi ${short(job.id, 18)}`;
  dom.modalEyebrow.textContent = operationLabel(job.operation);
  dom.modalBody.replaceChildren(node('div', { class: 'loading' }, node('div', { class: 'spinner' })));
  dom.modalActions.replaceChildren(button('Zamknij', closeModal));
  dom.modal.showModal();
  try {
    const result = await api(`/jobs/${job.id}/logs?limit=200`);
    const text = result.items.map(item => `[${formatDate(item.timestamp)}] ${item.message}`).join('\n') || 'Brak logów.';
    dom.modalBody.replaceChildren(node('div', { class: 'log-output mono', text }));
  } catch (error) { dom.modalBody.replaceChildren(node('p', { class: 'form-error', text: error.message })); }
}

registerView({ id: 'deployments', label: 'Wdrożenia', icon: 'D', permission: 'deployments.read', order: 110 }, deploymentsView);
registerView({ id: 'jobs', label: 'Zadania', icon: 'J', permission: 'jobs.read', order: 120 }, jobsView);
})();
