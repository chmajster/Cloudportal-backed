'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function renderSchemeCards(context) {
    const { state, data, rerender, saveStateFromInput, canCreate = false } = context;
    const core = parts.core;
    const pendingRow = () => state.pendingHostnameScheme ? {
      id: '__pending__',
      ...state.pendingHostnameScheme,
      is_active: true,
      pending: true,
    } : null;
    const syncPendingRow = () => {
      data.schemes = (data.schemes || []).filter(value => String(value.id) !== '__pending__');
      const row = pendingRow();
      if (row) data.schemes.push(row);
    };
    syncPendingRow();
    const schemes = data.schemes.filter(value => value.is_active);
    const selected = String(state.hostnameSchemeId || '');

    const schemePicker = selectField(
      'Pattern hostname z Generatora',
      'hostname_scheme_picker',
      [
        { value: '', label: schemes.length ? 'Wybierz pattern hostname' : 'Brak aktywnych patternów hostname' },
        ...schemes.map(scheme => ({
          value: scheme.id,
          label: (scheme.name || ('Pattern #' + scheme.id)) + ' — ' + scheme.pattern,
        })),
      ],
      selected,
      {
        required: state.hostnameEnabled && schemes.length > 0,
        wide: true,
        help: 'Lista zawiera aktywne patterny zapisane w Narzędzia → Generator hostname.',
      }
    );
    schemePicker.querySelector('select').addEventListener('change', event => {
      state.hostnameSchemeId = String(event.currentTarget.value || '');
      state.hostnameValues = { ...state.hostnameValues };
      state.creatingScheme = false;
      rerender();
    });

    const refreshSchemes = async () => {
      try {
        const result = await api('/hostname-schemes?limit=200', {
          headers: core.scopeHeaders(state),
        });
        data.schemes = result.items || [];
        syncPendingRow();
        const activeSchemes = data.schemes.filter(value => value.is_active);
        const stillAvailable = activeSchemes.some(value =>
          String(value.id) === String(state.hostnameSchemeId || ''));
        if (!stillAvailable) {
          state.hostnameSchemeId = String(activeSchemes[0]?.id || '');
        }
        toast('Lista patternów hostname odświeżona.');
        rerender();
      } catch (error) {
        toast(error.message, 'error');
      }
    };

    const grid = node('div', { class: 'blueprint-wizard-card-grid' });
    if (!schemes.length) {
      grid.append(node('div', { class: 'blueprint-wizard-empty' },
        node('strong', { text: 'Brak aktywnych patternów hostname' }),
        node('p', { class: 'muted', text: 'Utwórz pattern w tym wizardzie albo przejdź do Generatora hostname.' })));
    }

    for (const scheme of schemes) {
      const active = String(scheme.id) === selected;
      const card = node('button', {
        type: 'button',
        class: 'blueprint-wizard-select-card' + (active ? ' selected' : ''),
        'aria-pressed': String(active),
      },
        node('span', { class: 'blueprint-wizard-select-card-icon' }, appIcon('network')),
        node('span', { class: 'blueprint-wizard-select-card-copy' },
          node('strong', { text: scheme.name || ('Pattern #' + scheme.id) }),
          node('code', { text: scheme.pattern }),
          node('small', { text: 'Podgląd: ' + core.hostnameExample(scheme, state.hostnameValues) }),
          node('small', { text: 'Następny numer: ' + String(scheme.next_number || 1) })),
        active ? node('span', { class: 'blueprint-wizard-card-check' }, appIcon('check')) : null
      );
      card.addEventListener('click', () => {
        state.hostnameSchemeId = String(scheme.id);
        state.hostnameValues = { ...state.hostnameValues };
        rerender();
      });
      grid.append(card);
    }

    const selectedScheme = schemes.find(value => String(value.id) === String(state.hostnameSchemeId));
    const tokenFields = node('div', { class: 'form-grid blueprint-wizard-token-grid' });
    if (selectedScheme) {
      const tokens = core.hostnameTokens(selectedScheme.pattern);
      if (!tokens.length) {
        tokenFields.append(node('div', { class: 'blueprint-wizard-info wide' },
          node('strong', { text: 'Pattern jest w pełni automatyczny.' }),
          node('span', { text: 'Nie wymaga dodatkowych wartości domyślnych.' })));
      } else {
        const globalTokens = tokens.filter(token => ['location', 'role'].includes(token));
        if (globalTokens.length) {
          tokenFields.append(node('div', { class: 'blueprint-wizard-info wide' },
            node('strong', { text: 'Location i Role są ustawiane globalnie.' }),
            node('span', { text: globalTokens.map(token =>
              '{' + token + '}=' + (state.hostnameValues[token] || '—')).join(' · ') + '. Zmienisz je w Narzędzia → Location i Role.' })));
        }
        for (const token of tokens.filter(token => !['location', 'role'].includes(token))) {
          if (state.selectEnvironmentOnExecute && ['env', 'environment'].includes(token)) {
            tokenFields.append(node('div', { class: 'blueprint-wizard-info wide' },
              node('strong', { text: 'Environment w hostname będzie ustawiony przy tworzeniu VM.' }),
              node('span', { text: 'Wartość {' + token + '} zostanie pobrana z Environment wybranego podczas uruchomienia Blueprintu.' })));
            continue;
          }
          const wrapper = field(core.HOSTNAME_LABELS[token] || token, 'hostname_value_' + token, {
            value: state.hostnameValues[token] || '',
            required: true,
            placeholder: token === 'env' || token === 'environment' ? 'prod' : token,
            help: 'Wartość domyślna dla {' + token + '}.',
          });
          const input = wrapper.querySelector('input,select,textarea');
          input.addEventListener('input', event => {
            state.hostnameValues[token] = event.currentTarget.value.trim();
            const preview = tokenFields.querySelector('[data-hostname-live-preview]');
            if (preview) preview.textContent = core.hostnameExample(selectedScheme, state.hostnameValues);
          });
          tokenFields.append(wrapper);
        }
        tokenFields.append(node('div', { class: 'hostname-pattern-preview wide' },
          node('span', { class: 'field-label', text: 'Przykładowy hostname' }),
          node('strong', { class: 'mono', 'data-hostname-live-preview': 'true', text: core.hostnameExample(selectedScheme, state.hostnameValues) }),
          node('small', { class: 'field-help', text: 'Rzeczywisty numer zostanie zarezerwowany dopiero podczas wykonania Blueprintu.' })));
      }
    }

    const createPanel = node('div', { class: 'blueprint-wizard-inline-panel', hidden: !state.creatingScheme },
      field('Nazwa patternu', 'new_scheme_name', {
        value: state.newSchemeName,
        placeholder: 'Np. Wrocław PROD WEB',
        required: state.creatingScheme,
      }),
      field('Pattern', 'new_scheme_pattern', {
        value: state.newSchemePattern,
        placeholder: '{location}-{env}-{role}-{number}',
        required: state.creatingScheme,
        wide: true,
      }),
      field('Pierwszy numer', 'new_scheme_next', {
        type: 'number', min: 1, max: 999999999, value: state.newSchemeNext,
      }),
      field('Liczba cyfr', 'new_scheme_padding', {
        type: 'number', min: 1, max: 9, value: state.newSchemePadding,
      }),
      node('div', { class: 'blueprint-wizard-inline-actions wide' },
        button('Anuluj', () => { state.creatingScheme = false; rerender(); }, 'ghost'),
        button('Zapisz pattern', () => {
          const name = createPanel.querySelector('[name="new_scheme_name"]').value.trim();
          const pattern = createPanel.querySelector('[name="new_scheme_pattern"]').value.trim();
          const next = Number(createPanel.querySelector('[name="new_scheme_next"]').value || 1);
          const padding = Number(createPanel.querySelector('[name="new_scheme_padding"]').value || 3);
          if (!name || !pattern) {
            toast('Podaj nazwę i pattern hostname.', 'error');
            return;
          }
          state.pendingHostnameScheme = {
            name,
            pattern,
            next_number: next,
            padding,
            is_active: true,
          };
          syncPendingRow();
          state.hostnameSchemeId = '__pending__';
          state.creatingScheme = false;
          state.newSchemeName = '';
          toast('Pattern zostanie utworzony atomowo razem z Blueprintem.');
          rerender();
        }, 'primary'))
    );

    createPanel.querySelectorAll('input,textarea').forEach(control => {
      control.addEventListener('input', () => {
        saveStateFromInput(control);
      });
    });

    return node('div', { class: 'blueprint-wizard-step-stack' },
      node('div', { class: 'blueprint-wizard-switch-row' },
        node('div', {},
          node('strong', { text: 'Automatyczny hostname' }),
          node('span', { class: 'muted', text: 'Rezerwuje kolejny hostname i ustawia go jako nazwę deploymentu oraz VM.' })),
        checkboxField('', 'hostname_enabled', state.hostnameEnabled)),
      state.hostnameEnabled
        ? node('div', { class: 'blueprint-wizard-step-stack' },
            node('div', { class: 'blueprint-wizard-step-stack' },
              schemePicker,
              node('div', { class: 'blueprint-wizard-inline-actions' },
                button('Odśwież patterny', refreshSchemes, 'ghost'),
                button('Otwórz Generator hostname', () => {
                  window.open(location.pathname + '#hostnames', '_blank', 'noopener');
                }, 'ghost'))),
            grid,
            selectedScheme ? tokenFields : node('div', { class: 'blueprint-wizard-info', text: 'Wybierz pattern hostname z listy powyżej.' }),
            node('div', { class: 'blueprint-wizard-inline-actions' },
              canCreate
                ? button('Utwórz nowy pattern', () => { state.creatingScheme = true; rerender(); }, 'ghost')
                : null),
            canCreate ? createPanel : null)
        : field('Stała nazwa VM', 'manual_vm_name', {
            value: state.manualVmName,
            required: true,
            placeholder: 'np. web-prod-01',
            help: 'Pole jest widoczne tylko wtedy, gdy automatyczny hostname jest wyłączony.',
          })
    );
  }

  function captureHostname(root, state) {
    const enabled = root.querySelector('[name="hostname_enabled"]');
    if (enabled) state.hostnameEnabled = enabled.checked;
    const manual = root.querySelector('[name="manual_vm_name"]');
    if (manual) state.manualVmName = manual.value.trim();
    root.querySelectorAll('[name^="hostname_value_"]').forEach(input => {
      state.hostnameValues[input.name.replace('hostname_value_', '')] = input.value.trim();
    });
  }

  function validateHostname(state, data) {
    const errors = {};
    if (state.hostnameEnabled) {
      const scheme = data.schemes.find(value => String(value.id) === String(state.hostnameSchemeId));
      if (!scheme) {
        errors.hostnameSchemeId = 'Wybierz pattern hostname.';
      } else {
        for (const token of parts.core.hostnameTokens(scheme.pattern)) {
          if (['location', 'role'].includes(token)) continue;
          if (state.selectEnvironmentOnExecute && ['env', 'environment'].includes(token)) continue;
          if (!String(state.hostnameValues[token] || '').trim()) {
            errors['hostname_value_' + token] = 'Uzupełnij wartość dla {' + token + '}.';
          }
        }
      }
    } else if (!state.manualVmName) {
      errors.manualVmName = 'Podaj nazwę VM.';
    }
    return errors;
  }

  parts.hostname = { renderSchemeCards, captureHostname, validateHostname };
  registerExtension('blueprint-wizard-hostname', () => {});
})();
