'use strict';

(() => {
function customPlaybookVariablesText(value) {
  return JSON.stringify(value || {}, null, 2);
}

async function customPlaybookForm(item = null, seed = null) {
  try {
    const detail = item
      ? await api('/ansible/custom-playbooks/' + encodeURIComponent(item.id))
      : (seed || {
          id: '',
          name: '',
          description: '',
          category: 'Własne',
          transport: 'ssh',
          variables: {},
          wait_for_connection: true,
          validate_after: true,
          content: '- name: Własny playbook\n  hosts: all\n  become: true\n  tasks:\n    - name: Przykładowe zadanie\n      ansible.builtin.debug:\n        msg: "Cloudportal custom playbook"\n',
        });

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
        navigate('ansible-playbooks');
      },
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

function ansiblePlaybookRoleLabel(role) {
  return ({
    main: 'Główny playbook',
    wait: 'Oczekiwanie na połączenie',
    validate: 'Walidacja po wykonaniu',
  })[role] || role || 'Playbook';
}

async function copySystemPlaybook(item) {
  try {
    const source = await api('/ansible/playbooks/' + encodeURIComponent(item.id) + '/source');
    const files = source.files || [];
    const main = files.find(file => file.role === 'main') || files[0];
    if (!main?.content) throw new Error('Playbook systemowy nie zawiera głównego pliku YAML.');

    const required = new Set(item.required_variables || []);
    const variables = Object.fromEntries((item.variables || []).map(name => [
      name,
      { required: required.has(name), pattern: '.{1,8192}' },
    ]));
    const suffix = '-custom';
    const baseId = String(item.id || 'playbook').slice(0, Math.max(1, 63 - suffix.length));

    closeModal();
    await customPlaybookForm(null, {
      id: baseId + suffix,
      name: (item.name || item.id) + ' — kopia',
      description: 'Edytowalna kopia systemowego playbooka „' + (item.name || item.id) + '”.',
      category: item.category || 'Własne',
      transport: item.transport || 'ssh',
      variables,
      wait_for_connection: files.some(file => file.role === 'wait'),
      validate_after: files.some(file => file.role === 'validate'),
      content: main.content,
    });
  } catch (error) {
    toast(error.message, 'error');
  }
}

async function setSystemPlaybookEnabled(item, enabled) {
  try {
    await api('/catalog/playbooks/' + encodeURIComponent(item.id) + '/enabled', {
      method: 'PUT',
      body: { enabled },
    });
    toast((enabled ? 'Przywrócono' : 'Wyłączono') + ' systemowy playbook „' + item.name + '”.');
    navigate('ansible-playbooks');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function removeSystemPlaybookFromUse(item) {
  if (item.enabled === false) {
    return setSystemPlaybookEnabled(item, true);
  }
  return confirmAction(
    'Usuń systemowy playbook z użycia',
    'Playbook „' + item.name + '” jest dostarczany z aplikacją, więc jego plik pozostanie w instalacji. Zostanie wyłączony w katalogu i nie będzie można uruchamiać go w nowych zadaniach. Można go później przywrócić.',
    async () => {
      await api('/catalog/playbooks/' + encodeURIComponent(item.id) + '/enabled', {
        method: 'PUT',
        body: { enabled: false },
      });
      toast('Systemowy playbook został usunięty z użycia.');
      navigate('ansible-playbooks');
    },
  );
}

async function showAnsiblePlaybookPreview(item) {
  try {
    const source = await api('/ansible/playbooks/' + encodeURIComponent(item.id) + '/source');
    const files = source.files || [];
    const body = node('div', { class: 'ansible-playbook-preview-body' });

    const meta = node('div', { class: 'ansible-playbook-preview-meta' },
      badge(item.custom ? 'Własny' : 'Systemowy', item.custom ? 'warning' : 'info'),
      badge('v' + (source.version || item.version || 1)),
      badge(String(source.transport || item.transport || '').toUpperCase()),
      badge(item.enabled === false ? 'Wyłączony' : 'Aktywny', item.enabled === false ? 'danger' : 'ok'));

    const intro = node('div', { class: 'ansible-playbook-info' },
      node('div', { class: 'ansible-playbook-info-copy' },
        node('div', { class: 'ansible-playbook-info-title' },
          node('strong', { text: item.name || source.name || item.id })),
        item.description ? node('p', { text: item.description }) : null,
        node('small', { class: 'mono muted', text: item.id }),
        node('small', { class: 'muted', text: 'Zmienne: ' + ((item.variables || []).join(', ') || 'brak') })));

    if (!files.length) {
      body.append(meta, intro, node('div', { class: 'muted', text: 'Brak plików YAML do wyświetlenia.' }));
    } else {
      const tabs = node('div', { class: 'ansible-playbook-tabs', role: 'tablist', 'aria-label': 'Pliki playbooka Ansible' });
      const role = node('div', { class: 'ansible-playbook-role muted' });
      const code = node('pre', { class: 'ansible-playbook-code mono', tabindex: '0' });

      const selectFile = (file, tab) => {
        tabs.querySelectorAll('button').forEach(value => {
          const selected = value === tab;
          value.classList.toggle('active', selected);
          value.setAttribute('aria-selected', String(selected));
        });
        role.textContent = ansiblePlaybookRoleLabel(file.role) + ' · ' + (file.size ? formatBytes(file.size) : '—');
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

      body.append(meta, intro, tabs, role, code);
      selectFile(files[0], tabs.querySelector('button'));
    }

    dom.modal.classList.add('modal-wide');
    dom.modalTitle.textContent = 'Podgląd playbooka Ansible';
    dom.modalEyebrow.textContent = (item.custom ? 'Własny' : 'Systemowy') + ' · ' + item.id;
    dom.modalBody.replaceChildren(body);

    const actions = [button('Zamknij', closeModal)];

    if (item.enabled !== false && allowed('jobs.execute') && allowed('ansible.execute') && allowed('credentials.read')) {
      actions.unshift(button('Uruchom', () => {
        closeModal();
        if (!hasCommand('ansible.run')) {
          toast('Uruchamianie Ansible nie jest dostępne.', 'error');
          return;
        }
        runCommand('ansible.run', item.id);
      }, 'primary'));
    }

    if (item.custom && allowed('ansible.manage')) {
      actions.unshift(button('Edytuj', () => {
        closeModal();
        navigate('/admin/tools/ansible-playbooks/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'playbook'));
      }));
      actions.push(button('Usuń', () => {
        closeModal();
        deleteCustomPlaybook(item);
      }, 'danger'));
    } else if (!item.custom) {
      if (allowed('ansible.manage')) {
        actions.unshift(button('Edytuj kopię', () => copySystemPlaybook(item)));
      }
      if (allowed('settings.update')) {
        actions.push(button(item.enabled === false ? 'Przywróć do użycia' : 'Usuń z użycia', () => {
          closeModal();
          removeSystemPlaybookFromUse(item);
        }, item.enabled === false ? 'primary' : 'danger'));
      }
    }

    dom.modalActions.replaceChildren(...actions);
    if (!(typeof window.modalSurfaceOpen === 'function' ? window.modalSurfaceOpen() : dom.modal.open)) {
      dom.modal.showModal();
    }
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
    navigate('ansible-playbooks');
  } catch (error) {
    toast(error.message, 'error');
  }
}

function deleteCustomPlaybook(item) {
  return confirmAction(
    'Usuń własny playbook',
    'Playbook „' + item.name + '” zostanie usunięty z listy playbooków. Już utworzone joby zachowują snapshot użytej wersji.',
    async () => {
      await api('/ansible/custom-playbooks/' + encodeURIComponent(item.id), { method: 'DELETE' });
      toast('Własny playbook został usunięty.');
      navigate('ansible-playbooks');
    },
  );
}

async function ansiblePlaybooksView() {
  const playbooks = (await api('/ansible/playbooks')).items || [];
  const actions = [button('← Narzędzia', () => navigate('tools'))];
  if (allowed('ansible.manage')) {
    actions.push(button('Dodaj własny playbook Ansible', () => navigate('/admin/tools/ansible-playbooks/new'), 'primary'));
  }

  dom.content.replaceChildren(
    heading('Playbooki Ansible są zarządzane jako osobne narzędzie. Widok obejmuje playbooki systemowe i własne, ich wersje, status oraz operacje administracyjne.', actions),
    node('section', { class: 'panel' },
      node('div', { class: 'panel-header' },
        node('div', {},
          node('h2', { text: 'Playbooki Ansible' }),
          node('p', { class: 'muted', text: 'Playbooki systemowe oraz własne wersjonowane definicje dodane przez administratorów Ansible.' }))),
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
      ], playbooks, item => {
        const rowActions = [
          button('Podgląd', () => showAnsiblePlaybookPreview(item)),
        ];
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
          rowActions.push(button('Edytuj', () => navigate('/admin/tools/ansible-playbooks/edit/' + encodeURIComponent(item.id) + '/' + encodeURIComponent(item.name || 'playbook'))));
          rowActions.push(button(item.enabled === false ? 'Włącz' : 'Wyłącz', () => toggleCustomPlaybook(item), item.enabled === false ? 'primary' : 'danger'));
          rowActions.push(button('Usuń', () => deleteCustomPlaybook(item), 'danger'));
        } else if (!item.custom) {
          if (allowed('ansible.manage')) {
            rowActions.push(button('Edytuj kopię', () => copySystemPlaybook(item)));
          }
          if (allowed('settings.update')) {
            rowActions.push(button(
              item.enabled === false ? 'Przywróć do użycia' : 'Usuń z użycia',
              () => removeSystemPlaybookFromUse(item),
              item.enabled === false ? 'primary' : 'danger'
            ));
          }
        }
        return rowActions;
      }))
  );
}

registerRoutedForm({
  id: 'ansible-playbooks-create',
  pattern: /^\/admin\/tools\/ansible-playbooks\/new$/,
  parent: 'ansible-playbooks',
  permission: 'ansible.manage',
  label: 'Playbooki Ansible',
}, () => customPlaybookForm());
registerRoutedForm({
  id: 'ansible-playbooks-edit',
  pattern: /^\/admin\/tools\/ansible-playbooks\/edit\/(?<id>[^/]+)(?:\/[^/]+)?$/,
  parent: 'ansible-playbooks',
  permission: 'ansible.manage',
  label: 'Playbooki Ansible',
}, async match => {
  const playbooks = (await api('/ansible/playbooks')).items;
  const item = playbooks.find(value => String(value.id) === String(match.params.id) && value.custom);
  if (!item) throw new Error('Nie znaleziono własnego playbooka.');
  await customPlaybookForm(item);
});

registerView({
  id: 'ansible-playbooks',
  label: 'Playbooki Ansible',
  iconName: 'file-text',
  navigation: false,
  navigationParent: 'tools',
  permission: 'ansible.read',
  order: 160,
}, ansiblePlaybooksView);

})();
