'use strict';

(() => {
function openProxmoxTemplateWizard(item = null) {
  if (!hasCommand('blueprints.proxmoxTemplateWizard')) {
    toast('Kreator szablonu IaC nie jest dostępny.', 'error');
    return;
  }
  return runCommand('blueprints.proxmoxTemplateWizard', item, { mode: 'template', returnTo: 'catalog' });
}

function canManageCatalogTemplate(item) {
  const required = new Set((item?.manager_role_ids || []).map(Number));
  if (!required.size) return true;
  const owned = new Set((state.identity?.roles || []).map(role => Number(role.id)));
  return [...required].some(id => owned.has(id));
}

async function toggleGeneratedTemplate(item) {
  try {
    await api('/blueprints/' + item.id + '/enabled', {
      method: 'PUT',
      body: { enabled: !item.is_active },
    });
    toast((item.is_active ? 'Wyłączono' : 'Włączono') + ' szablon „' + item.name + '”.');
    navigate('catalog');
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toggleCatalogItem(kind, item) {
  const enabled = item.enabled === false;
  const label = kind === 'templates' ? 'szablon' : 'playbook';
  try {
    await api('/catalog/' + kind + '/' + encodeURIComponent(item.id) + '/enabled', {
      method: 'PUT',
      body: { enabled },
    });
    toast((enabled ? 'Włączono ' : 'Wyłączono ') + label + ' „' + item.name + '”.');
    navigate('catalog');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function customPlaybookVariablesText(value) {
  return JSON.stringify(value || {}, null, 2);
}

async function customPlaybookForm(item = null) {
  try {
    const detail = item
      ? await api('/ansible/custom-playbooks/' + encodeURIComponent(item.id))
      : {
          id: '',
          name: '',
          description: '',
          category: 'Własne',
          transport: 'ssh',
          variables: {},
          wait_for_connection: true,
          validate_after: true,
          content: '- name: Własny playbook\n  hosts: all\n  become: true\n  tasks:\n    - name: Przykładowe zadanie\n      ansible.builtin.debug:\n        msg: "Cloudportal custom playbook"\n',
        };

    const idField = field('ID playbooka', 'id', {
      required: true,
      value: detail.id || '',
      placeholder: 'my-playbook',
      help: 'Stały identyfikator, np. linux-hardening. Po utworzeniu nie można go zmienić.',
    });
    if (item) idField.querySelector('input').disabled = true;

    const yamlField = field('Playbook YAML', 'content', {
      tag: 'textarea',
      required: true,
      wide: true,
      value: detail.content || '',
      help: 'Maks. 256 KiB. Każdy play musi używać hosts: all. Operacje wykonywane lokalnie na kontrolerze są blokowane.',
    });
    const yamlInput = yamlField.querySelector('textarea');
    yamlInput.rows = 18;

    const variablesField = field('Zmienne wejściowe (JSON)', 'variables', {
      tag: 'textarea',
      wide: true,
      value: customPlaybookVariablesText(detail.variables),
      help: 'Format: {"nazwa":{"required":true,"pattern":"[A-Za-z0-9_.-]{1,64}"}}. Pusty obiekt oznacza brak zmiennych.',
    });
    variablesField.querySelector('textarea').rows = 7;

    const fileInput = node('input', {
      type: 'file',
      accept: '.yml,.yaml,text/yaml,text/plain',
      hidden: true,
    });
    fileInput.addEventListener('change', async () => {
      const file = fileInput.files?.[0];
      if (!file) return;
      if (file.size > 262144) {
        toast('Plik playbooka przekracza 256 KiB.', 'error');
        fileInput.value = '';
        return;
      }
      yamlInput.value = await file.text();
    });

    const uploadRow = node('div', { class: 'wide row-actions' },
      button('Wczytaj plik .yml / .yaml', () => fileInput.click()),
      fileInput);

    const body = node('div', { class: 'form-grid' },
      node('div', { class: 'wide form-error warning', text: 'Własny playbook jest kodem administracyjnym uruchamianym na wskazanych VM. Tworzenie i edycja wymagają uprawnienia ansible.manage.' }),
      idField,
      field('Nazwa', 'name', { required: true, value: detail.name || '' }),
      field('Kategoria', 'category', { required: true, value: detail.category || 'Własne' }),
      selectField('Transport', 'transport', [
        { value: 'ssh', label: 'SSH · Linux/Unix' },
        { value: 'winrm', label: 'WinRM · Windows' },
      ], detail.transport || 'ssh', { required: true }),
      field('Opis', 'description', {
        tag: 'textarea',
        wide: true,
        value: detail.description || '',
      }),
      checkboxField('Poczekaj na dostępność połączenia przed playbookiem', 'wait_for_connection', detail.wait_for_connection !== false),
      checkboxField('Zweryfikuj połączenie po wykonaniu', 'validate_after', detail.validate_after !== false),
      variablesField,
      yamlField,
      uploadRow);

    openModal({
      title: item ? 'Edytuj własny playbook Ansible' : 'Dodaj własny playbook Ansible',
      eyebrow: item ? ('Ansible · ' + detail.id + ' · v' + detail.version) : 'Ansible · własny katalog',
      body,
      submitLabel: item ? 'Zapisz nową wersję' : 'Dodaj playbook',
      wide: true,
      onSubmit: async data => {
        let variables;
        try {
          variables = JSON.parse(String(data.get('variables') || '{}'));
        } catch {
          throw new Error('Zmienne wejściowe muszą być poprawnym obiektem JSON.');
        }
        if (!variables || typeof variables !== 'object' || Array.isArray(variables)) {
          throw new Error('Zmienne wejściowe muszą być obiektem JSON.');
        }
        const id = item ? detail.id : String(data.get('id') || '').trim();
        const payload = {
          id,
          name: String(data.get('name') || '').trim(),
          description: String(data.get('description') || '').trim(),
          category: String(data.get('category') || '').trim(),
          transport: data.get('transport'),
          variables,
          wait_for_connection: data.has('wait_for_connection'),
          validate_after: data.has('validate_after'),
          content: String(data.get('content') || ''),
        };
        await api('/ansible/custom-playbooks' + (item ? '/' + encodeURIComponent(detail.id) : ''), {
          method: item ? 'PUT' : 'POST',
          body: payload,
        });
        toast(item ? 'Własny playbook zapisany jako nowa wersja.' : 'Własny playbook został dodany.');
        navigate('catalog');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function toggleCustomPlaybook(item) {
  try {
    const enabled = item.enabled === false;
    await api('/ansible/custom-playbooks/' + encodeURIComponent(item.id) + '/enabled', {
      method: 'PUT',
      body: { enabled },
    });
    toast((enabled ? 'Włączono' : 'Wyłączono') + ' własny playbook „' + item.name + '”.');
    navigate('catalog');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function deleteCustomPlaybook(item) {
  return confirmAction(
    'Usuń własny playbook',
    'Playbook „' + item.name + '” zostanie usunięty z katalogu. Już utworzone joby zachowują snapshot użytej wersji.',
    async () => {
      await api('/ansible/custom-playbooks/' + encodeURIComponent(item.id), { method: 'DELETE' });
      toast('Własny playbook został usunięty.');
      navigate('catalog');
    },
  );
}

async function catalogView() {
  const [templates, playbooks, blueprintResult, roleResult] = await Promise.all([
    api('/templates'),
    allowed('ansible.read') ? api('/ansible/playbooks') : Promise.resolve({ items: [] }),
    allowed('blueprints.read') ? api('/blueprints?limit=200') : Promise.resolve({ items: [] }),
    allowed('roles.read') ? api('/roles?limit=200') : Promise.resolve({ items: [] }),
  ]);
  const rolesById = new Map(roleResult.items.map(role => [Number(role.id), role.name]));

  const proxmoxModuleEnabled = templates.items.some(item => item.id === 'proxmox-vm' && item.enabled !== false);
  const canCreateTemplate = proxmoxModuleEnabled
    && allowed('blueprints.create')
    && allowed('providers.read')
    && allowed('credentials.read')
    && allowed('hostnames.read')
    && allowed('hostnames.create')
    && allowed('ipam.read');

  const actions = [];
  if (canCreateTemplate) {
    actions.push(button('Nowy szablon Terraform / OpenTofu', () => openProxmoxTemplateWizard(), 'primary'));
  }
  if (allowed('ansible.manage')) {
    actions.push(button('Dodaj własny playbook Ansible', () => navigate('/catalog/ansible/new'), 'primary'));
  }

  const sections = [
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Szablony Terraform / OpenTofu' }),
          node('p', { class: 'muted', text: 'Bazowe, zatwierdzone moduły IaC używane przez deploymenty i kreator.' }))),
      table([
        { label: 'Szablon', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.id })) },
        { label: 'Platforma', value: item => badge(CREDENTIAL_TYPE_CONFIG[item.provider]?.label || item.provider, 'info') },
        { label: 'Wersja', value: item => `v${item.version}` },
        { label: 'Status', value: item => badge(item.enabled === false ? 'Wyłączony' : 'Aktywny', item.enabled === false ? 'danger' : 'ok') },
        { label: 'Import', value: item => badge(item.importable ? 'Obsługiwany' : 'Tylko tworzenie', item.importable ? 'ok' : 'info') },
      ], templates.items, item => {
        const rowActions = [button('Pola', () => showTemplateFields(item))];
        if (allowed('settings.update')) {
          rowActions.push(button(item.enabled === false ? 'Włącz' : 'Wyłącz', () => toggleCatalogItem('templates', item), item.enabled === false ? 'primary' : 'danger'));
        }
        return rowActions;
      })
    ),
  ];

  if (allowed('blueprints.read')) {
    const generated = blueprintResult.items.filter(item => item.deployment?.template === 'proxmox-vm');
    sections.push(node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Szablony utworzone w kreatorze' }),
          node('p', { class: 'muted', text: 'Gotowe presety Proxmox z zapisaną VM bazową, parametrami, silnikiem IaC i generatorem hostname.' }))),
      table([
        { label: 'Nazwa', value: item => node('div', {}, node('strong', { text: item.name }), node('div', { class: 'mono muted', text: item.slug })) },
        { label: 'Silnik', value: item => badge(item.deployment?.executor === 'opentofu' ? 'OpenTofu' : 'Terraform', 'info') },
        { label: 'Provider', value: item => '#' + (item.deployment?.provider_id ?? '—') },
        { label: 'VM bazowa', value: item => {
          const nodeName = item.deployment?.variables?.template_node || item.deployment?.variables?.node || '—';
          const vmid = item.deployment?.variables?.template_id ?? '—';
          return nodeName + ' / VMID ' + vmid;
        } },
        { label: 'Wersja', value: item => 'v' + item.version },
        { label: 'Role zarządzające', value: item => (item.manager_role_ids || []).length
          ? (item.manager_role_ids || []).map(id => rolesById.get(Number(id)) || ('Rola #' + id)).join(', ')
          : 'Brak dedykowanej roli' },
        { label: 'Status', value: item => badge(item.is_active ? 'Aktywny' : 'Nieaktywny', item.is_active ? 'ok' : 'danger') },
      ], generated, item => {
        const rowActions = [];
        const canManage = canManageCatalogTemplate(item);
        if (allowed('blueprints.update') && canManage) {
          if (canCreateTemplate || item.is_active === false) {
            rowActions.push(button('Edytuj', () => openProxmoxTemplateWizard(item)));
          }
          rowActions.push(button(item.is_active ? 'Wyłącz' : 'Włącz', () => toggleGeneratedTemplate(item), item.is_active ? 'danger' : 'primary'));
        }
        const deploymentTemplateEnabled = templates.items.some(template =>
          template.id === item.deployment?.template && template.enabled !== false);
        if (allowed('blueprints.execute') && deploymentTemplateEnabled && item.is_active) {
          rowActions.push(button('Użyj', () => {
            if (!hasCommand('blueprints.execute')) {
              toast('Uruchamianie szablonu nie jest dostępne.', 'error');
              return;
            }
            runCommand('blueprints.execute', item);
          }, 'primary'));
        }
        return rowActions;
      })
    ));
  }

  if (allowed('ansible.read')) {
    sections.push(node('section', { class: 'panel' },
      node('div', { class: 'panel-header' }, node('div', {}, node('h2', { text: 'Playbooki Ansible' }), node('p', { class: 'muted', text: 'Playbooki systemowe oraz własne wersjonowane definicje dodane przez administratorów Ansible.' }))),
      table([
        { label: 'Playbook', value: item => node('div', {},
          node('strong', { text: item.name }),
          item.description ? node('div', { class: 'muted', text: item.description }) : null) },
        { label: 'Kategoria', value: item => badge(item.category || 'Inne', 'info') },
        { label: 'ID', class: 'mono', value: item => item.id },
        { label: 'Źródło', value: item => badge(item.custom ? 'Własny' : 'Systemowy', item.custom ? 'warning' : 'info') },
        { label: 'Transport', value: item => badge(item.transport.toUpperCase()) },
        { label: 'Status', value: item => badge(item.enabled === false ? 'Wyłączony' : 'Aktywny', item.enabled === false ? 'danger' : 'ok') },
        { label: 'Zmienne', value: item => item.variables.map(name => FIELD_LABELS[name] || name.replaceAll('_', ' ')).join(', ') || '—' },
      ], playbooks.items, item => {
        const rowActions = [];
        if (item.enabled !== false && allowed('jobs.execute') && allowed('ansible.execute') && allowed('credentials.read')) {
          rowActions.push(button('Uruchom', () => {
            if (!hasCommand('ansible.run')) {
              toast('Uruchamianie Ansible nie jest dostępne.', 'error');
              return;
            }
            runCommand('ansible.run', item.id);
          }, 'primary'));
        }
        if (item.custom && allowed('ansible.manage')) {
          rowActions.push(button('Edytuj', () => navigate('/catalog/ansible/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'playbook'))));
          rowActions.push(button(item.enabled === false ? 'Włącz' : 'Wyłącz', () => toggleCustomPlaybook(item), item.enabled === false ? 'primary' : 'danger'));
          rowActions.push(button('Usuń', () => deleteCustomPlaybook(item), 'danger'));
        } else if (!item.custom && allowed('settings.update')) {
          rowActions.push(button(item.enabled === false ? 'Włącz' : 'Wyłącz', () => toggleCatalogItem('playbooks', item), item.enabled === false ? 'primary' : 'danger'));
        }
        return rowActions;
      })));
  }

  dom.content.replaceChildren(
    heading('Katalog IaC i kreator wielokrotnie używalnych szablonów VM. Kreator nie przyjmuje arbitralnego HCL — generuje bezpieczny preset na bazie zatwierdzonego modułu Proxmox.', actions),
    ...sections,
  );
}

registerRoutedForm({
  id: 'catalog-ansible-create',
  pattern: /^\/catalog\/ansible\/new$/,
  parent: 'catalog',
  permission: 'ansible.manage',
  label: 'Katalog IaC',
}, () => customPlaybookForm());
registerRoutedForm({
  id: 'catalog-ansible-edit',
  pattern: /^\/catalog\/ansible\/edit\/(?<id>[^/]+)(?:\/[^/]+)?$/,
  parent: 'catalog',
  permission: 'ansible.manage',
  label: 'Katalog IaC',
}, async match => {
  const playbooks = (await api('/ansible/playbooks')).items;
  const item = playbooks.find(value => String(value.id) === String(match.params.id) && value.custom);
  if (!item) throw new Error('Nie znaleziono własnego playbooka.');
  await customPlaybookForm(item);
});
registerView({ id: 'catalog', label: 'Katalog IaC', icon: 'C', permission: 'terraform.read', order: 60 }, catalogView);
})();
