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


function deploymentVariableValue(container, name) {
  return container.querySelector('[name="' + name + '"]')?.value || '';
}

function deploymentVariableWrapper(container, name) {
  return container.querySelector('[data-template-variable="' + name + '"]');
}

function deploymentVariableRequired(template, name) {
  return new Set(template?.variables_schema?.required || []).has(name);
}

function deploymentSetSelectChoices(select, choices, selected = '', placeholder = '') {
  select.replaceChildren();
  if (placeholder) select.append(node('option', { value: '', text: placeholder }));
  choices.forEach(choice => select.append(node('option', {
    value: choice.value,
    text: choice.label,
    selected: String(choice.value) === String(selected),
  })));
  if (!select.value && choices.length === 1) select.value = String(choices[0].value);
}

function deploymentReplaceVariableWithSelect(container, template, name, label, choices, selected, placeholder, help) {
  const current = deploymentVariableWrapper(container, name);
  if (!current) return null;
  const replacement = selectField(label, name, choices, selected, {
    required: deploymentVariableRequired(template, name),
    placeholder,
  });
  replacement.setAttribute('data-template-variable', name);
  if (help) replacement.append(node('small', { class: 'field-help', text: help }));
  current.replaceWith(replacement);
  return replacement.querySelector('select');
}

function createProxmoxTemplatePicker(container, rows, selectedId = '', selectedNode = '', onSelect = null) {
  const current = deploymentVariableWrapper(container, 'template_id');
  if (!current) return null;

  const hiddenId = node('input', { type: 'hidden', name: 'template_id', value: selectedId });
  const hiddenNode = node('input', { type: 'hidden', name: 'template_node', value: selectedNode });
  const search = node('input', {
    type: 'search',
    class: 'proxmox-template-search',
    placeholder: 'Szukaj po nazwie, VMID lub węźle…',
    'aria-label': 'Szukaj szablonu Proxmox',
  });
  const nodeFilter = node('select', { class: 'proxmox-template-node-filter', 'aria-label': 'Filtruj szablony po węźle' });
  const nodes = [...new Set(rows.map(row => String(row.node || '')).filter(Boolean))].sort((a, b) => a.localeCompare(b));
  nodeFilter.append(node('option', { value: '', text: 'Wszystkie węzły' }));
  nodes.forEach(value => nodeFilter.append(node('option', { value, text: value })));

  const summary = node('div', { class: 'proxmox-template-selection', 'aria-live': 'polite' });
  const grid = node('div', { class: 'proxmox-template-grid', role: 'listbox', 'aria-label': 'Szablony Proxmox' });

  const selectTemplate = row => {
    hiddenId.value = String(row?.vmid || '');
    hiddenNode.value = String(row?.node || '');
    render();
    if (typeof onSelect === 'function') onSelect(row);
  };

  const render = () => {
    const query = search.value.trim().toLowerCase();
    const filterNode = nodeFilter.value;
    const visible = rows.filter(row => {
      if (filterNode && String(row.node || '') !== filterNode) return false;
      if (!query) return true;
      return [row.name, row.vmid, row.node]
        .map(value => String(value || '').toLowerCase())
        .some(value => value.includes(query));
    });

    grid.replaceChildren();
    visible.forEach(row => {
      const selected = String(row.vmid) === String(hiddenId.value)
        && String(row.node || '') === String(hiddenNode.value || '');
      const facts = [];
      if (Number.isFinite(Number(row.maxcpu)) && Number(row.maxcpu) > 0) facts.push(String(row.maxcpu) + ' vCPU');
      if (Number.isFinite(Number(row.maxmem)) && Number(row.maxmem) > 0) facts.push(formatBytes(row.maxmem) + ' RAM');
      if (Number.isFinite(Number(row.maxdisk)) && Number(row.maxdisk) > 0) facts.push(formatBytes(row.maxdisk) + ' dysk');

      const card = node('button', {
        type: 'button',
        class: 'proxmox-template-card' + (selected ? ' selected' : ''),
        role: 'option',
        'aria-selected': String(selected),
      },
        node('span', { class: 'proxmox-template-card-icon' }, appIcon('box')),
        node('span', { class: 'proxmox-template-card-copy' },
          node('strong', { text: row.name || ('VM template ' + row.vmid) }),
          node('span', { class: 'proxmox-template-card-meta' },
            node('span', { class: 'badge info', text: 'VMID ' + row.vmid }),
            node('span', { class: 'badge', text: row.node || 'brak węzła' })),
          facts.length ? node('small', { text: facts.join(' · ') }) : null),
        selected ? node('span', { class: 'proxmox-template-card-check', 'aria-hidden': 'true' }, appIcon('check')) : null
      );
      card.addEventListener('click', () => selectTemplate(row));
      grid.append(card);
    });

    if (!visible.length) {
      grid.append(node('div', { class: 'proxmox-template-empty', text: rows.length
        ? 'Brak szablonów pasujących do filtra.'
        : 'Na tej platformie Proxmox nie znaleziono szablonów QEMU.' }));
    }

    const selected = rows.find(row =>
      String(row.vmid) === String(hiddenId.value)
      && String(row.node || '') === String(hiddenNode.value || ''));
    summary.replaceChildren(
      selected
        ? node('div', { class: 'proxmox-template-selected-summary' },
          node('span', { class: 'proxmox-template-selected-icon' }, appIcon('check')),
          node('span', {},
            node('strong', { text: selected.name || ('VM template ' + selected.vmid) }),
            node('small', { text: 'VMID ' + selected.vmid + ' · węzeł źródłowy ' + (selected.node || '—') })))
        : node('span', { class: 'muted', text: 'Kliknij kafelek, aby wybrać bazowy szablon VM. VMID i węzeł źródłowy zostaną ustawione automatycznie.' })
    );
  };

  search.addEventListener('input', render);
  nodeFilter.addEventListener('change', render);

  const picker = node('section', {
    class: 'proxmox-template-picker wide',
    'data-template-variable': 'template_id',
  },
    hiddenId,
    hiddenNode,
    node('div', { class: 'proxmox-template-picker-header' },
      node('div', {},
        node('strong', { text: 'Obraz / szablon Proxmox' }),
        node('small', { text: 'Bez ręcznego wpisywania VMID. Wybierz szablon bezpośrednio z aktualnego inventory Proxmox.' }))),
    node('div', { class: 'proxmox-template-toolbar' }, search, nodeFilter),
    summary,
    grid
  );

  current.replaceWith(picker);
  const oldTemplateNode = deploymentVariableWrapper(container, 'template_node');
  if (oldTemplateNode && oldTemplateNode !== picker) oldTemplateNode.remove();

  const exact = rows.find(row =>
    String(row.vmid) === String(selectedId)
    && (!selectedNode || String(row.node || '') === String(selectedNode)));
  if (exact) selectTemplate(exact);
  else if (rows.length === 1) selectTemplate(rows[0]);
  else render();

  return { picker, hiddenId, hiddenNode };
}

async function enhanceProxmoxDeploymentVariables(container, template, providerSelect) {
  if (template?.provider !== 'proxmox') return;

  const providerId = providerSelect.value;
  if (!providerId) return;

  const previous = {
    node: deploymentVariableValue(container, 'node'),
    templateId: deploymentVariableValue(container, 'template_id'),
    templateNode: deploymentVariableValue(container, 'template_node'),
    storage: deploymentVariableValue(container, 'storage'),
    network: deploymentVariableValue(container, 'network') || 'vmbr0',
  };

  const [nodeResult, templateResult] = await Promise.all([
    api('/providers/' + providerId + '/nodes'),
    api('/providers/' + providerId + '/templates'),
  ]);

  const nodeChoices = nodeResult.items
    .filter(item => item.node)
    .map(item => ({
      value: item.node,
      label: item.node + (item.status ? ' · ' + statusLabel(item.status) : ''),
    }));

  const nodeSelect = deploymentReplaceVariableWithSelect(
    container,
    template,
    'node',
    'Węzeł / lokalizacja',
    nodeChoices,
    previous.node,
    'Wybierz docelowy węzeł',
    'Lista jest pobierana bezpośrednio z wybranej platformy Proxmox.'
  );

  const storageSelect = deploymentReplaceVariableWithSelect(
    container,
    template,
    'storage',
    'Storage',
    [],
    previous.storage,
    'Najpierw wybierz węzeł',
    'Pokazywane są storage dostępne na wybranym węźle.'
  );
  const networkSelect = deploymentReplaceVariableWithSelect(
    container,
    template,
    'network',
    'Sieć / bridge',
    [],
    previous.network,
    'Najpierw wybierz węzeł',
    'Bridge jest wybierany z sieci wykrytych na wybranym węźle.'
  );

  const loadNodeResources = async () => {
    if (!nodeSelect?.value) {
      if (storageSelect) deploymentSetSelectChoices(storageSelect, [], '', 'Wybierz docelowy węzeł');
      if (networkSelect) deploymentSetSelectChoices(networkSelect, [], '', 'Wybierz docelowy węzeł');
      return;
    }

    const keepStorage = storageSelect?.value || previous.storage;
    const keepNetwork = networkSelect?.value || previous.network || 'vmbr0';
    const [storageResult, networkResult] = await Promise.all([
      api('/providers/' + providerId + '/storages?node=' + encodeURIComponent(nodeSelect.value)),
      api('/providers/' + providerId + '/networks?node=' + encodeURIComponent(nodeSelect.value)),
    ]);

    if (storageSelect) {
      const storages = storageResult.items
        .filter(item => {
          if (item.disable) return false;
          const content = Array.isArray(item.content) ? item.content.join(',') : String(item.content || '');
          return !content || content.includes('images');
        })
        .map(item => ({
          value: item.storage || item.id,
          label: (item.storage || item.id || 'storage')
            + (item.type ? ' [' + item.type + ']' : '')
            + (Number.isFinite(Number(item.avail)) ? ' · wolne ' + formatBytes(item.avail) : ''),
        }));
      deploymentSetSelectChoices(storageSelect, storages, keepStorage, storages.length ? 'Wybierz storage' : 'Brak storage dla VM');
    }

    if (networkSelect) {
      const networks = networkResult.items
        .filter(item => item.iface)
        .map(item => ({
          value: item.iface,
          label: item.iface
            + (item.type ? ' [' + item.type + ']' : '')
            + (item.active === 1 || item.active === true ? ' · aktywna' : ''),
        }));
      const preferred = networks.some(item => String(item.value) === String(keepNetwork))
        ? keepNetwork
        : (networks.some(item => String(item.value) === 'vmbr0') ? 'vmbr0' : '');
      deploymentSetSelectChoices(networkSelect, networks, preferred, networks.length ? 'Wybierz bridge' : 'Brak sieci na węźle');
    }
  };

  if (nodeSelect) {
    nodeSelect.addEventListener('change', () => {
      loadNodeResources().catch(error => toast(error.message, 'error'));
    });
  }

  const picker = createProxmoxTemplatePicker(
    container,
    templateResult.items || [],
    previous.templateId,
    previous.templateNode,
    row => {
      if (!nodeSelect || !row?.node) return;
      const matchingTargetNode = [...nodeSelect.options].some(option => String(option.value) === String(row.node));
      if (!matchingTargetNode) {
        toast('Węzeł szablonu nie jest dostępny jako węzeł docelowy. Wybierz węzeł ręcznie.', 'warning');
        return;
      }
      if (!nodeSelect.value) nodeSelect.value = String(row.node);
      loadNodeResources().catch(error => toast(error.message, 'error'));
    }
  );

  if (nodeSelect && !nodeSelect.value && picker?.hiddenNode?.value) {
    const templateNode = String(picker.hiddenNode.value);
    const matchingTargetNode = [...nodeSelect.options].some(option => String(option.value) === templateNode);
    if (matchingTargetNode) nodeSelect.value = templateNode;
  }

  await loadNodeResources();
}


function createAnsiblePlaybookPreview(playbookSelect) {
  const body = node('div', { class: 'ansible-playbook-preview-body' },
    node('div', { class: 'muted', text: 'Kliknij „Podgląd playbooka”, aby wczytać YAML.' }));
  const panel = node('section', { class: 'ansible-playbook-preview wide', hidden: true },
    node('div', { class: 'ansible-playbook-preview-header' },
      node('div', {},
        node('strong', { text: 'Podgląd playbooka Ansible' }),
        node('small', { text: 'Widok tylko do odczytu. Pokazywane są zatwierdzone pliki YAML używane przez backend.' })),
      button('Ukryj podgląd', () => { panel.hidden = true; }, 'ghost')),
    body);
  let loadedId = null;

  const roleLabel = role => ({
    main: 'Główny playbook',
    wait: 'Oczekiwanie',
    validate: 'Walidacja',
  }[role] || role || 'Playbook');

  const render = source => {
    const files = source?.files || [];
    if (!files.length) {
      body.replaceChildren(node('div', { class: 'muted', text: 'Brak plików YAML do wyświetlenia.' }));
      return;
    }

    const meta = node('div', { class: 'ansible-playbook-preview-meta' },
      node('span', { class: 'badge info', text: source.name || source.id }),
      node('span', { class: 'badge', text: 'v' + source.version }),
      node('span', { class: 'badge', text: String(source.transport || '').toUpperCase() }));
    const tabs = node('div', { class: 'ansible-playbook-tabs', role: 'tablist', 'aria-label': 'Pliki playbooka Ansible' });
    const role = node('div', { class: 'ansible-playbook-role muted' });
    const code = node('pre', { class: 'ansible-playbook-code mono', tabindex: '0' });

    const selectFile = (file, tab) => {
      tabs.querySelectorAll('button').forEach(value => {
        value.classList.toggle('active', value === tab);
        value.setAttribute('aria-selected', String(value === tab));
      });
      role.textContent = roleLabel(file.role);
      code.textContent = file.content || '';
      code.setAttribute('aria-label', 'Kod playbooka ' + file.name);
    };

    files.forEach((file, index) => {
      const tab = node('button', {
        type: 'button',
        class: 'ansible-playbook-tab' + (index === 0 ? ' active' : ''),
        role: 'tab',
        'aria-selected': String(index === 0),
        text: file.name,
      });
      tab.addEventListener('click', () => selectFile(file, tab));
      tabs.append(tab);
    });

    body.replaceChildren(meta, tabs, role, code);
    selectFile(files[0], tabs.querySelector('button'));
  };

  const load = async () => {
    const playbookId = playbookSelect.value;
    if (!playbookId) throw new Error('Wybierz playbook Ansible.');
    panel.hidden = false;
    if (loadedId === playbookId && body.querySelector('.ansible-playbook-code')) return;
    loadedId = playbookId;
    body.replaceChildren(node('div', { class: 'loading ansible-playbook-preview-loading' }, node('div', { class: 'spinner' })));
    try {
      const source = await api('/ansible/playbooks/' + encodeURIComponent(playbookId) + '/source');
      render(source);
    } catch (error) {
      loadedId = null;
      body.replaceChildren(node('div', { class: 'form-error', text: error.message }));
    }
  };

  const actions = node('div', { class: 'ansible-playbook-preview-actions wide' },
    button('Podgląd playbooka', () => load().catch(error => toast(error.message, 'error')), 'ghost'));

  playbookSelect.addEventListener('change', () => {
    loadedId = null;
    if (!panel.hidden) load().catch(error => toast(error.message, 'error'));
  });

  return { actions, panel };
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

    const terraformPreviewBody = node('div', { class: 'terraform-template-preview-body' },
      node('div', { class: 'muted', text: 'Kliknij „Podgląd Terraform”, aby wczytać pliki HCL wybranego szablonu.' }));
    const terraformPreview = node('section', { class: 'terraform-template-preview wide', hidden: true },
      node('div', { class: 'terraform-template-preview-header' },
        node('div', {},
          node('strong', { text: 'Podgląd szablonu Terraform / OpenTofu' }),
          node('small', { text: 'Widok tylko do odczytu. Pokazywany jest dokładny HCL używany przez backend.' })),
        button('Ukryj podgląd', () => { terraformPreview.hidden = true; }, 'ghost')),
      terraformPreviewBody);
    let terraformPreviewTemplateId = null;

    const renderTerraformPreview = source => {
      const files = source?.files || [];
      if (!files.length) {
        terraformPreviewBody.replaceChildren(node('div', { class: 'muted', text: 'Szablon nie zawiera plików .tf.' }));
        return;
      }
      const tabs = node('div', { class: 'terraform-template-tabs', role: 'tablist', 'aria-label': 'Pliki Terraform' });
      const code = node('pre', { class: 'terraform-template-code mono', tabindex: '0' });
      const meta = node('div', { class: 'terraform-template-preview-meta' },
        node('span', { class: 'badge info', text: source.name || source.id }),
        node('span', { class: 'badge', text: 'v' + source.version }),
        node('span', { class: 'badge', text: CREDENTIAL_TYPE_CONFIG[source.provider]?.label || source.provider }));

      const selectFile = (file, tab) => {
        tabs.querySelectorAll('button').forEach(value => {
          value.classList.toggle('active', value === tab);
          value.setAttribute('aria-selected', String(value === tab));
        });
        code.textContent = file.content || '';
        code.setAttribute('aria-label', 'Kod pliku ' + file.name);
      };

      files.forEach((file, index) => {
        const tab = node('button', {
          type: 'button',
          class: 'terraform-template-tab' + (index === 0 ? ' active' : ''),
          role: 'tab',
          'aria-selected': String(index === 0),
          text: file.name,
        });
        tab.addEventListener('click', () => selectFile(file, tab));
        tabs.append(tab);
      });

      terraformPreviewBody.replaceChildren(meta, tabs, code);
      selectFile(files[0], tabs.querySelector('button'));
    };

    const loadTerraformPreview = async () => {
      const templateId = templateField.querySelector('select').value;
      if (!templateId) throw new Error('Wybierz szablon Terraform.');
      terraformPreview.hidden = false;
      if (terraformPreviewTemplateId === templateId && terraformPreviewBody.querySelector('.terraform-template-code')) return;
      terraformPreviewTemplateId = templateId;
      terraformPreviewBody.replaceChildren(node('div', { class: 'loading terraform-template-preview-loading' }, node('div', { class: 'spinner' })));
      try {
        renderTerraformPreview(await api('/templates/' + encodeURIComponent(templateId) + '/source'));
      } catch (error) {
        terraformPreviewTemplateId = null;
        terraformPreviewBody.replaceChildren(node('div', { class: 'form-error', text: error.message }));
      }
    };

    const terraformPreviewButton = button('Podgląd Terraform', () => {
      loadTerraformPreview().catch(error => toast(error.message, 'error'));
    }, 'ghost');

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
          node('div', { class: 'terraform-template-preview-actions wide' }, terraformPreviewButton),
          terraformPreview,
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
      const playbookSelect = playbookField.querySelector('select');
      const playbookPreview = createAnsiblePlaybookPreview(playbookSelect);
      const credentialField = selectField('Systemowe dane dostępowe', 'ansible_credentials_id', [], '', { required: enabled });
      const variablesContainer = node('div', { class: 'form-grid wide' });
      ansibleFields.replaceChildren(playbookField, playbookPreview.actions, playbookPreview.panel, credentialField, variablesContainer);
      ansibleFields.querySelectorAll('input,select,textarea').forEach(control => { control.disabled = !enabled; });

      const refreshPlaybook = () => {
        const playbook = playbooks.find(value => value.id === playbookSelect.value) || playbooks[0];
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
      playbookSelect.addEventListener('change', refreshPlaybook);
      refreshPlaybook();
    };

    const refreshTemplate = async () => {
      const template = currentTemplate();
      if (!template) return;
      const matchingProviders = providers.filter(item => item.type === template.provider).map(item => ({ id: item.id, label: item.name }));
      refill(providerSelect, matchingProviders, matchingProviders.length ? 'Wybierz provider' : 'Brak połączenia z tą platformą');
      refreshCredentials();
      renderTemplateVariables(variableFields, template);
      await enhanceProxmoxDeploymentVariables(variableFields, template, providerSelect);
      renderAnsible();
    };

    templateSelect.addEventListener('change', () => {
      terraformPreviewTemplateId = null;
      if (!terraformPreview.hidden) {
        loadTerraformPreview().catch(error => toast(error.message, 'error'));
      }
      refreshTemplate().catch(error => toast(error.message, 'error'));
    });
    providerSelect.addEventListener('change', () => {
      refreshCredentials();
      enhanceProxmoxDeploymentVariables(variableFields, currentTemplate(), providerSelect)
        .catch(error => toast(error.message, 'error'));
    });
    ansibleToggle.querySelector('input').addEventListener('change', renderAnsible);
    await refreshTemplate();

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
        if (template?.provider === 'proxmox' && !form.elements.template_id?.value) {
          throw new Error('Wybierz bazowy szablon Proxmox z kreatora.');
        }
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
    const playbookSelect = playbookField.querySelector('select');
    const playbookPreview = createAnsiblePlaybookPreview(playbookSelect);
    const credentialField = selectField('Dane dostępowe hosta', 'credentials_id', [], '', { required: true });
    const variables = node('div', { class: 'form-grid wide' });
    const fields = node('div', { class: 'form-grid' },
      formSection('Cel', 'Podaj adresy IP hostów, na których ma zostać uruchomiony zatwierdzony playbook.',
        node('div', { class: 'form-grid' },
          playbookField,
          playbookPreview.actions,
          playbookPreview.panel,
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
      const playbook = playbooks.find(value => value.id === playbookSelect.value) || playbooks[0];
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
    playbookSelect.addEventListener('change', refresh);
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

registerCommand('deployments.create', createDeployment);
registerCommand('deployments.open', showDeploymentDetails);
registerView({ id: 'deployments', label: 'Wdrożenia', icon: 'D', permission: 'deployments.read', order: 110 }, deploymentsView);
registerView({ id: 'jobs', label: 'Zadania', icon: 'J', permission: 'jobs.read', order: 120 }, jobsView);
})();
