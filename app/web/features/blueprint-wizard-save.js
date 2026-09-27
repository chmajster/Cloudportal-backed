'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

  function create(context) {
    const {
      state,
      data,
      options,
      editingItem,
      pageMode,
      steps,
      blueprintScope,
      bodyRoot,
      footerRoot,
      captureCurrentStep,
      validateStep,
      render,
      replaceBlueprintWizardRoute,
    } = context;

    function normalizedBlueprintIdentity(value) {
      return String(value || '').trim().toLocaleLowerCase('pl-PL');
    }

    async function findExistingBlueprintConflicts(payload) {
      const conflicts = new Map();
      const targetName = normalizedBlueprintIdentity(payload?.name);
      const targetSlug = normalizedBlueprintIdentity(payload?.slug);
      let offset = 0;
      while (true) {
        const result = await api('/blueprints?limit=200&offset=' + offset, {
          headers: parts.core.scopeHeaders(state),
        });
        const items = Array.isArray(result?.items) ? result.items : [];
        for (const item of items) {
          const sameName = targetName && normalizedBlueprintIdentity(item.name) === targetName;
          const sameSlug = targetSlug && normalizedBlueprintIdentity(item.slug) === targetSlug;
          if (sameName || sameSlug) {
            conflicts.set(String(item.id), { item, sameName, sameSlug });
          }
        }
        if (items.length < 200) break;
        offset += items.length;
      }
      return [...conflicts.values()];
    }

    function renderBlueprintSaveSuccess(saved, replaced = false) {
      window.CloudportalBlueprintScope = {
        tenant_id: String(state.tenantId),
        project_id: String(state.projectId),
      };
      if (pageMode) replaceBlueprintWizardRoute(saved, state, options);
      bodyRoot.replaceChildren(
        node('div', { class: 'blueprint-wizard-submit-progress blueprint-wizard-submit-success' },
          node('span', { class: 'blueprint-wizard-success-icon' }, appIcon('check')),
          node('h3', {
            text: replaced
              ? 'Istniejący produkt został zastąpiony i zapisany jako nowa wersja.'
              : editingItem
                ? 'Blueprint został zaktualizowany i zapisany jako nowa wersja.'
                : 'Blueprint został utworzony i jest gotowy do użycia.',
          }),
          node('p', { class: 'muted', text: saved.name + ' · v' + saved.version }),
          node('div', { class: 'blueprint-wizard-inline-actions' },
            button('Zamknij', () => navigate('blueprints'), 'primary'),
            blueprintScope.allows('blueprints.execute')
              ? button('Przejdź do Blueprintów', () => navigate('blueprints'))
              : null))
      );
      footerRoot.replaceChildren();
      state.submitting = false;
    }

    async function replaceExistingBlueprint(conflict, payload, inlineHostnameScheme) {
      const existing = conflict.item;
      state.submitting = true;
      footerRoot.replaceChildren(button('Zastępowanie…', () => {}, 'danger', true));
      bodyRoot.replaceChildren(node('div', { class: 'blueprint-wizard-submit-progress' },
        node('div', { class: 'spinner' }),
        node('strong', { text: 'Zastępowanie istniejącego produktu' }),
        node('span', { text: existing.name + ' · v' + existing.version })));
      try {
        const requestPath = inlineHostnameScheme
          ? `/blueprints/${existing.id}/bundle`
          : `/blueprints/${existing.id}`;
        const requestBody = inlineHostnameScheme
          ? { blueprint: payload, hostname_scheme: inlineHostnameScheme }
          : payload;
        const updated = await api(requestPath, {
          method: 'PUT',
          body: requestBody,
          headers: {
            ...parts.core.scopeHeaders(state),
            'If-Match': String(existing.version),
          },
        });
        renderBlueprintSaveSuccess(updated, true);
      } catch (error) {
        state.submitting = false;
        state.errors = { submit: error.message };
        state.step = steps.length - 1;
        render();
      }
    }

    function renderExistingBlueprintDecision(conflicts, payload, inlineHostnameScheme) {
      state.submitting = false;
      const uniqueConflict = conflicts.length === 1 ? conflicts[0] : null;
      const existing = uniqueConflict?.item || null;
      const canReplace = Boolean(
        existing
        && blueprintScope.allows('blueprints.update')
        && existing.can_manage !== false
      );
      const reasons = uniqueConflict
        ? [
            uniqueConflict.sameName ? 'ta sama nazwa' : null,
            uniqueConflict.sameSlug ? 'ten sam slug' : null,
          ].filter(Boolean).join(' i ')
        : 'nazwa i slug wskazują różne istniejące Blueprinty';

      const actions = node('div', { class: 'blueprint-wizard-inline-actions' },
        canReplace
          ? button('Zastąp istniejący produkt', () =>
              replaceExistingBlueprint(uniqueConflict, payload, inlineHostnameScheme), 'danger')
          : null,
        button('Wróć i zmień nazwę', () => {
          state.step = 0;
          state.errors = {};
          render();
        }, 'ghost'));

      bodyRoot.replaceChildren(node('section', {
        class: 'panel blueprint-wizard-replace-existing',
        role: 'alert',
      },
        node('div', { class: 'blueprint-wizard-replace-icon', 'aria-hidden': 'true' }, appIcon('workflow')),
        node('div', { class: 'stack' },
          node('div', {},
            node('p', { class: 'eyebrow', text: 'Produkt już istnieje' }),
            node('h3', { text: existing ? existing.name : 'Konflikt istniejących Blueprintów' })),
          node('p', {
            text: existing
              ? `W tym projekcie istnieje już Blueprint z konfliktem: ${reasons}. Możesz zastąpić istniejący produkt nową konfiguracją.`
              : 'Nie można bezpiecznie wybrać jednego produktu do zastąpienia, ponieważ nazwa i slug kolidują z różnymi Blueprintami.',
          }),
          existing
            ? node('dl', { class: 'blueprint-wizard-replace-meta' },
                node('dt', { text: 'Istniejący produkt' }),
                node('dd', { text: existing.name }),
                node('dt', { text: 'Slug' }),
                node('dd', { class: 'mono', text: existing.slug }),
                node('dt', { text: 'Wersja' }),
                node('dd', { text: 'v' + existing.version }))
            : null,
          !canReplace && existing
            ? node('p', { class: 'muted', text:
                'Nie masz uprawnienia blueprints.update lub roli zarządzającej tym produktem. Zmień nazwę/slug albo poproś administratora o aktualizację istniejącego Blueprintu.' })
            : null,
          actions)
      ));
      footerRoot.replaceChildren();
    }

    async function submitBlueprint() {
      captureCurrentStep();
      for (let index = 0; index < steps.length - 1; index += 1) {
        if (!validateStep(index)) {
          state.step = index;
          render();
          return;
        }
      }
      let payload;
      try {
        payload = parts.core.buildPayload(state, data);
      } catch (error) {
        state.errors = { payload: error.message };
        render();
        return;
      }
      state.submitting = true;
      footerRoot.replaceChildren(button('Zapisywanie…', () => {}, 'primary', true));
      const progress = node('div', { class: 'blueprint-wizard-submit-progress' },
        node('div', { class: 'spinner' }),
        node('strong', { text: editingItem ? 'Aktualizacja Blueprintu' : 'Tworzenie Blueprintu' }),
        node('span', { text: 'Walidacja konfiguracji…' }));
      bodyRoot.replaceChildren(progress);
      try {
        const inlineHostnameScheme = state.hostnameSchemeId === '__pending__'
          ? state.pendingHostnameScheme
          : null;

        if (!editingItem) {
          progress.querySelector('span').textContent = 'Sprawdzanie, czy produkt już istnieje…';
          const conflicts = await findExistingBlueprintConflicts(payload);
          if (conflicts.length) {
            renderExistingBlueprintDecision(conflicts, payload, inlineHostnameScheme);
            return;
          }
        }

        progress.querySelector('span').textContent = 'Zapisywanie definicji i workflow…';
        const requestPath = inlineHostnameScheme
          ? (editingItem ? `/blueprints/${editingItem.id}/bundle` : '/blueprints/bundle')
          : (editingItem ? `/blueprints/${editingItem.id}` : '/blueprints');
        const requestBody = inlineHostnameScheme
          ? { blueprint: payload, hostname_scheme: inlineHostnameScheme }
          : payload;
        const saved = await api(requestPath, {
          method: editingItem ? 'PUT' : 'POST',
          body: requestBody,
          headers: {
            ...parts.core.scopeHeaders(state),
            ...(editingItem ? { 'If-Match': String(editingItem.version) } : {}),
          },
          idempotent: !editingItem,
        });
        renderBlueprintSaveSuccess(saved, false);
      } catch (error) {
        if (!editingItem && Number(error?.status) === 409) {
          try {
            const inlineHostnameScheme = state.hostnameSchemeId === '__pending__'
              ? state.pendingHostnameScheme
              : null;
            const conflicts = await findExistingBlueprintConflicts(payload);
            if (conflicts.length) {
              renderExistingBlueprintDecision(conflicts, payload, inlineHostnameScheme);
              return;
            }
          } catch {
            // Keep the original API error if conflict discovery itself fails.
          }
        }
        state.submitting = false;
        state.errors = { submit: error.message };
        state.step = steps.length - 1;
        render();
      }
    }


    return Object.freeze({ submitBlueprint });
  }

  parts.save = { create };
  registerExtension('blueprint-wizard-save', () => {});
})();
