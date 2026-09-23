'use strict';

(() => {
  function autoValue(item) {
    if (item?.auto_approve_for_executors == null) return 'inherit';
    return item.auto_approve_for_executors ? 'true' : 'false';
  }

  function fields(item = null) {
    return [
      selectField('Auto-approval', 'auto_approve_for_executors', [
        { value: 'inherit', label: 'Dziedzicz z projektu / ustawień globalnych' },
        { value: 'true', label: 'Włączone dla tego Blueprintu' },
        { value: 'false', label: 'Wyłączone dla tego Blueprintu' },
      ], autoValue(item)),
      field('Timeout approval (h)', 'approval_timeout_hours', {
        type: 'number',
        min: 1,
        max: 720,
        value: item?.approval_timeout_hours ?? '',
        help: 'Puste pole oznacza dziedziczenie timeoutu z projektu, a następnie z ustawienia globalnego.',
      }),
    ];
  }

  function wizardFields(state) {
    return fields({
      auto_approve_for_executors: state.autoApproveForExecutors === 'inherit'
        ? null
        : state.autoApproveForExecutors === 'true',
      approval_timeout_hours: state.approvalTimeoutHours,
    });
  }

  function parseAuto(value) {
    return value === 'inherit' || value == null || value === '' ? null : value === 'true';
  }

  function parseTimeout(value) {
    const normalized = String(value ?? '').trim();
    return normalized ? Number(normalized) : null;
  }

  function badgeFor(item) {
    if (!item.requires_approval) return badge('Bez approval', 'info');
    const ownPolicy = item.auto_approve_for_executors != null || item.approval_timeout_hours != null;
    return badge(ownPolicy ? 'Approval wg polityki Blueprintu' : 'Approval wg projektu/globalnej', 'warning');
  }

  function autoSummary(value) {
    if (value === 'inherit') return 'Dziedziczony z projektu / globalnie';
    return value === 'true' ? 'Włączony w Blueprintcie' : 'Wyłączony w Blueprintcie';
  }

  function timeoutSummary(value) {
    return String(value ?? '').trim() ? value + ' h' : 'Dziedziczony z projektu / globalnie';
  }

  window.BlueprintApprovalPolicyUI = Object.freeze({
    fields,
    wizardFields,
    parseAuto,
    parseTimeout,
    badgeFor,
    autoSummary,
    timeoutSummary,
  });

  registerExtension('blueprint-approval-policy-ui', () => {});
})();
