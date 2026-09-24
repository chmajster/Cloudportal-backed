'use strict';

(() => {
  function cloneRun(run = {}) {
    return {
      playbook: String(run.playbook || ''),
      credentials_id: run.credentials_id ? Number(run.credentials_id) : null,
      variables: { ...(run.variables || {}) },
    };
  }

  function initialRunsFromDeployment(deployment = {}) {
    if (Array.isArray(deployment.ansible_runs) && deployment.ansible_runs.length) {
      return deployment.ansible_runs.map(cloneRun);
    }
    return deployment.ansible ? [cloneRun(deployment.ansible)] : [];
  }

  function createEditor(options = {}) {
    const playbooks = options.playbooks || [];
    const credentials = options.credentials || [];
    const editable = options.editable !== false;
    const autoVariables = { ...(options.autoVariables || {}) };
    const prefix = options.prefix || 'deployment_ansible';
    let supported = options.supported !== false;
    let runs = (options.initialRuns || []).map(cloneRun);

    const toggle = checkboxField(
      options.toggleLabel || 'Po wdrożeniu uruchom zatwierdzone runbooki Ansible',
      prefix + '_enabled',
      options.enabled ?? runs.length > 0,
    );
    const toggleInput = toggle.querySelector('input');
    toggleInput.disabled = !editable || !supported;

    const body = node('div', { class: 'blueprint-ansible-runs wide' });
    const section = formSection(
      options.title || 'Konfiguracja systemu po wdrożeniu',
      options.description || 'Runbooki są wykonywane kolejno po utworzeniu VM i wykryciu jej adresu.',
      toggle,
      body,
    );

    function savedPlaybook(run) {
      const credential = credentials.find(value => Number(value.id) === Number(run.credentials_id));
      return {
        id: run.playbook,
        name: run.playbook + ' (zapisany)',
        version: '?',
        transport: credential?.type || 'ssh',
        variables: Object.keys(run.variables || {}),
        required_variables: [],
      };
    }

    function playbookChoices(run) {
      const result = [...playbooks];
      if (run.playbook && !result.some(value => value.id === run.playbook)) {
        result.unshift(savedPlaybook(run));
      }
      return result;
    }

    function defaultRun() {
      const playbook = playbooks[0];
      const matching = playbook
        ? credentials.filter(value => value.type === playbook.transport)
        : [];
      return {
        playbook: playbook?.id || '',
        credentials_id: matching[0]?.id ? Number(matching[0].id) : null,
        variables: {},
      };
    }

    function cleanRun(run) {
      const playbook = playbookChoices(run).find(value => value.id === run.playbook);
      const variables = {};
      for (const name of playbook?.variables || Object.keys(run.variables || {})) {
        if (Object.prototype.hasOwnProperty.call(autoVariables, name)) {
          variables[name] = autoVariables[name];
          continue;
        }
        const value = run.variables?.[name];
        if (value !== undefined && value !== null && String(value).trim() !== '') {
          variables[name] = String(value).trim();
        }
      }
      return {
        playbook: run.playbook,
        credentials_id: Number(run.credentials_id),
        variables,
      };
    }

    function getRuns({ validate = true } = {}) {
      if (!toggleInput.checked || !supported) return [];
      const result = runs.map(cleanRun);
      if (!validate) return result;
      if (!result.length) throw new Error('Dodaj co najmniej jeden runbook Ansible.');
      result.forEach((run, index) => {
        const playbook = playbookChoices(runs[index]).find(value => value.id === run.playbook);
        if (!run.playbook || !playbook) {
          throw new Error('Runbook #' + (index + 1) + ': wybierz playbook.');
        }
        if (!Number.isInteger(run.credentials_id) || run.credentials_id <= 0) {
          throw new Error('Runbook #' + (index + 1) + ': wybierz systemowe dane dostępowe.');
        }
        for (const name of playbook.required_variables || []) {
          if (Object.prototype.hasOwnProperty.call(autoVariables, name)) continue;
          if (!String(run.variables?.[name] || '').trim()) {
            throw new Error('Runbook #' + (index + 1) + ': uzupełnij zmienną ' + name + '.');
          }
        }
      });
      return result;
    }

    function move(index, delta) {
      const target = index + delta;
      if (target < 0 || target >= runs.length) return;
      const [item] = runs.splice(index, 1);
      runs.splice(target, 0, item);
      render();
      options.onChange?.(getRuns({ validate: false }));
    }

    function remove(index) {
      runs.splice(index, 1);
      if (!runs.length && toggleInput.checked) runs.push(defaultRun());
      render();
      options.onChange?.(getRuns({ validate: false }));
    }

    function runCard(run, index) {
      const choices = playbookChoices(run);
      const selectedPlaybook = choices.find(value => value.id === run.playbook) || choices[0] || null;
      if (!run.playbook && selectedPlaybook) run.playbook = selectedPlaybook.id;

      const playbookField = selectField(
        'Runbook / Playbook #' + (index + 1),
        prefix + '_playbook_' + index,
        choices.map(value => ({
          value: value.id,
          label: value.name + (value.version ? ' · v' + value.version : ''),
        })),
        run.playbook,
        { required: true, placeholder: 'Wybierz runbook' },
      );
      const playbookSelect = playbookField.querySelector('select');

      const credentialField = selectField(
        'Systemowe dane dostępowe',
        prefix + '_credentials_' + index,
        [],
        run.credentials_id || '',
        { required: true, placeholder: 'Wybierz systemowe dane dostępowe' },
      );
      const credentialSelect = credentialField.querySelector('select');

      const variableFields = node('div', { class: 'form-grid wide' });

      function refreshCredentialOptions(playbook, keep = true) {
        const matching = credentials.filter(value => value.type === playbook?.transport);
        credentialSelect.replaceChildren(node('option', {
          value: '',
          text: matching.length ? 'Wybierz systemowe dane dostępowe' : 'Brak zgodnych danych dostępowych',
        }));
        matching.forEach(value => credentialSelect.append(node('option', {
          value: value.id,
          text: value.name + ' [' + value.type + '] (#' + value.id + ')',
          selected: keep && Number(value.id) === Number(run.credentials_id),
        })));
        if (!credentialSelect.value && matching.length === 1) {
          credentialSelect.value = String(matching[0].id);
        }
        run.credentials_id = credentialSelect.value ? Number(credentialSelect.value) : null;
      }

      function refreshVariables(playbook) {
        variableFields.replaceChildren();
        const names = playbook?.variables || [];
        if (!names.length) {
          variableFields.append(node('p', {
            class: 'muted wide',
            text: 'Ten runbook nie wymaga dodatkowych zmiennych.',
          }));
          return;
        }
        const required = new Set(playbook.required_variables || []);
        names.forEach(name => {
          if (Object.prototype.hasOwnProperty.call(autoVariables, name)) {
            run.variables[name] = autoVariables[name];
            variableFields.append(node('div', { class: 'blueprint-wizard-info wide' },
              node('strong', { text: name }),
              node('span', { text: 'Automatycznie: ' + autoVariables[name] })));
            return;
          }
          const wrapper = field(name, prefix + '_var_' + index + '_' + name, {
            value: run.variables?.[name] || '',
            required: required.has(name),
            help: required.has(name) ? 'Zmienna wymagana przez runbook.' : 'Opcjonalna zmienna runbooka.',
          });
          const input = wrapper.querySelector('input,textarea');
          input.addEventListener('input', () => {
            run.variables[name] = input.value;
            options.onChange?.(getRuns({ validate: false }));
          });
          if (!editable) input.disabled = true;
          variableFields.append(wrapper);
        });
      }

      refreshCredentialOptions(selectedPlaybook, true);
      refreshVariables(selectedPlaybook);

      playbookSelect.addEventListener('change', () => {
        run.playbook = playbookSelect.value;
        run.variables = {};
        const selected = choices.find(value => value.id === run.playbook) || null;
        refreshCredentialOptions(selected, false);
        refreshVariables(selected);
        options.onChange?.(getRuns({ validate: false }));
      });
      credentialSelect.addEventListener('change', () => {
        run.credentials_id = credentialSelect.value ? Number(credentialSelect.value) : null;
        options.onChange?.(getRuns({ validate: false }));
      });

      if (!editable) {
        playbookSelect.disabled = true;
        credentialSelect.disabled = true;
      }

      const actions = [];
      if (editable) {
        actions.push(
          button('W górę', () => move(index, -1), 'ghost'),
          button('W dół', () => move(index, 1), 'ghost'),
          button('Usuń runbook', () => remove(index), 'danger'),
        );
        actions[0].disabled = index === 0;
        actions[1].disabled = index === runs.length - 1;
      }

      return node('div', { class: 'editor-card blueprint-ansible-run-card', 'data-ansible-run': String(index) },
        node('div', { class: 'editor-card-header' },
          node('strong', { text: 'Runbook ' + (index + 1) + ' z ' + runs.length }),
          node('div', { class: 'editor-actions' }, ...actions)),
        node('div', { class: 'form-grid' }, playbookField, credentialField, variableFields));
    }

    function render() {
      section.hidden = !supported;
      toggleInput.disabled = !editable || !supported;
      if (!supported) return;
      if (!toggleInput.checked) {
        body.replaceChildren(node('p', {
          class: 'muted wide',
          text: 'Po wdrożeniu nie zostanie uruchomiony żaden runbook Ansible.',
        }));
        return;
      }
      if (!runs.length) runs.push(defaultRun());
      const headerActions = editable
        ? node('div', { class: 'blueprint-wizard-inline-actions' },
            button('Dodaj runbook', () => {
              if (runs.length >= 20) {
                toast('Możesz dodać maksymalnie 20 runbooków.', 'error');
                return;
              }
              runs.push(defaultRun());
              render();
              options.onChange?.(getRuns({ validate: false }));
            }, 'primary'))
        : null;
      body.replaceChildren(
        node('div', { class: 'blueprint-wizard-section-heading wide' },
          node('strong', { text: 'Kolejność wykonania' }),
          node('span', { class: 'muted', text: 'Runbooki wykonują się od góry do dołu. Zakończenie jednego uruchamia następny.' }),
          headerActions),
        ...runs.map(runCard),
      );
    }

    toggleInput.addEventListener('change', () => {
      if (toggleInput.checked && !runs.length) runs.push(defaultRun());
      render();
      options.onChange?.(getRuns({ validate: false }));
    });

    function setSupported(value) {
      supported = Boolean(value);
      render();
    }

    render();
    return {
      section,
      toggle,
      getRuns,
      setSupported,
      isEnabled: () => supported && toggleInput.checked,
      snapshot: () => runs.map(cloneRun),
    };
  }

  registerExtension('blueprint-ansible-runs', () => {
    window.BlueprintAnsibleRuns = Object.freeze({
      cloneRun,
      initialRunsFromDeployment,
      createEditor,
    });
  });
})();
