'use strict';

(() => {
  const parts = window.BlueprintWizardParts = window.BlueprintWizardParts || {};

function validateWorkflow(state, editingItem) {
  const errors = {};
  if (!state.advancedWorkflow) {
    Object.assign(errors, parts.cloudInit.validate(state));
    return errors;
  }
  const steps = state.workflow || [];
  const fail = message => {
    if (!errors.workflow) errors.workflow = message;
  };
  if (!steps.length) {
    errors.workflow = 'Workflow musi zawierać co najmniej jeden krok.';
    return errors;
  }

  const ids = steps.map(value => String(value.id || ''));
  if (ids.some(value => !value)) fail('Każdy krok workflow musi mieć ID.');
  if (new Set(ids).size !== ids.length) fail('ID kroków workflow muszą być unikalne.');
  const known = new Set(ids.filter(Boolean));
  const byId = new Map(steps.filter(step => step.id).map(step => [step.id, step]));
  const allowedConditionKeys = new Set(['provider', 'executor', 'has_ansible', 'hostname', 'environment', 'apmid']);

  for (const step of steps) {
    if (step.type === 'create_vm') fail('create_vm nie jest obsługiwanym krokiem workflow.');
    if (step.conditions?.__invalid) fail('Conditions muszą być poprawnym obiektem JSON.');
    if ((step.depends_on || []).some(value => !known.has(value) || value === step.id)) {
      fail('Workflow zawiera brakującą zależność albo zależność do samego siebie.');
    }
    const allowed = new Set(allowedConditionKeys);
    if (step.type === 'delay') allowed.add('seconds');
    if (step.type === 'notification') allowed.add('message');
    const conditions = step.conditions || {};
    const unknownConditions = Object.keys(conditions).filter(key => !allowed.has(key));
    if (unknownConditions.length) {
      fail('Krok ' + step.id + ' używa nieobsługiwanych conditions: ' + unknownConditions.join(', ') + '.');
    }
    if ('has_ansible' in conditions && typeof conditions.has_ansible !== 'boolean') {
      fail('Condition has_ansible musi być wartością boolean.');
    }
    for (const key of ['provider', 'executor', 'hostname', 'environment', 'apmid']) {
      if (!(key in conditions)) continue;
      const value = conditions[key];
      const valid = typeof value === 'string'
        || (Array.isArray(value) && value.length && value.every(item => typeof item === 'string' && item));
      if (!valid) fail('Condition ' + key + ' musi być stringiem albo niepustą listą stringów.');
    }
    if (step.type === 'delay') {
      const seconds = conditions.seconds ?? 1;
      if (typeof seconds !== 'number' || !Number.isFinite(seconds) || seconds < 0 || seconds > Number(step.timeout || 600)) {
        fail('Delay seconds musi być liczbą od 0 do timeout kroku.');
      }
    }
    if (step.type === 'notification') {
      const message = conditions.message ?? step.id;
      if (typeof message !== 'string' || message.length > 1000) {
        fail('Notification message musi być tekstem do 1000 znaków.');
      }
    }
  }

  const visiting = new Set();
  const visited = new Set();
  const visit = id => {
    if (visiting.has(id)) return false;
    if (visited.has(id) || !byId.has(id)) return true;
    visiting.add(id);
    for (const parent of (byId.get(id).depends_on || [])) {
      if (!visit(parent)) return false;
    }
    visiting.delete(id);
    visited.add(id);
    return true;
  };
  if ([...known].some(id => !visit(id))) fail('Workflow musi być acyklicznym grafem DAG.');

  const ancestors = id => {
    const result = new Set();
    const pending = [...(byId.get(id)?.depends_on || [])];
    while (pending.length) {
      const parent = pending.pop();
      if (result.has(parent)) continue;
      result.add(parent);
      pending.push(...(byId.get(parent)?.depends_on || []));
    }
    return result;
  };

  const rollbackTargets = new Set(steps.map(step => step.rollback).filter(Boolean));
  const safeRollbackTypes = new Set(['terraform_destroy', 'notification', 'delay']);
  for (const target of rollbackTargets) {
    const rollback = byId.get(target);
    if (!rollback) {
      fail('Rollback wskazuje nieistniejący krok: ' + target + '.');
      continue;
    }
    if (!safeRollbackTypes.has(rollback.type)) fail('Rollback może wskazywać tylko terraform_destroy, notification lub delay.');
    if ((rollback.depends_on || []).length) fail('Krok używany wyłącznie do rollbacku nie może mieć zwykłych zależności.');
    if (steps.some(step => (step.depends_on || []).includes(target))) fail('Krok rollback nie może być zależnością normalnego workflow.');
  }
  for (const step of steps) {
    if (step.rollback === step.id) fail('Krok workflow nie może wykonywać rollbacku do samego siebie.');
    if (step.type === 'terraform_destroy' && !rollbackTargets.has(step.id)) {
      fail('terraform_destroy jest dozwolony wyłącznie jako cel rollbacku.');
    }
    if (step.type === 'release_ip') fail('release_ip nie jest dozwolony podczas provisioningu VM.');
  }

  const compileTime = new Set(['generate_hostname', 'allocate_ip']);
  for (const step of steps) {
    if (compileTime.has(step.type) && (
      Object.keys(step.conditions || {}).length || Number(step.retry || 0) || step.rollback
    )) fail('Kroki compile-time nie mogą mieć conditions, retry ani rollback.');
  }

  const approvalSteps = steps.filter(step => step.type === 'approval');
  if (approvalSteps.length > 1) fail('Workflow może zawierać maksymalnie jeden krok approval.');
  if (approvalSteps.length && !state.requiresApproval) fail('Krok approval wymaga włączenia „Wymaga zatwierdzenia”.');
  if (approvalSteps.some(step => Object.keys(step.conditions || {}).length || Number(step.retry || 0) || step.rollback)) {
    fail('Krok approval nie może mieć conditions, retry ani rollback.');
  }

  const applySteps = steps.filter(step => step.type === 'terraform_apply');
  const planSteps = steps.filter(step => step.type === 'terraform_plan');
  if (planSteps.length > 1) fail('Workflow może zawierać maksymalnie jeden terraform_plan.');

  const directProxmox = state.executor === 'proxmox';
  const directOnly = new Set(['clone_vm', 'configure_vm', 'start_vm', 'set_hostname', 'set_tags']);
  if (directProxmox) {
    if (steps.some(step => ['terraform_plan', 'terraform_apply'].includes(step.type))) {
      fail('Tryb Proxmox API nie może zawierać kroków Terraform plan/apply.');
    }
    const cloneSteps = steps.filter(step => step.type === 'clone_vm');
    if (cloneSteps.length !== 1) fail('Tryb Proxmox API wymaga dokładnie jednego kroku clone_vm.');
    if (approvalSteps.length) fail('Tryb Proxmox API używa akceptacji całego joba; usuń jawny krok approval.');
    if (cloneSteps.length === 1) {
      const cloneId = cloneSteps[0].id;
      const afterClone = new Set([
        'configure_vm', 'cloud_init', 'start_vm', 'set_hostname', 'set_tags',
        'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
        'run_ansible_playbook', 'register_awx', 'create_snapshot', 'health_check',
      ]);
      for (const step of steps) {
        if (afterClone.has(step.type) && !ancestors(step.id).has(cloneId)) {
          fail(step.type + ' musi zależeć od clone_vm w trybie Proxmox API.');
        }
      }
      const cloud = steps.find(step => step.type === 'cloud_init');
      for (const startStep of steps.filter(step => step.type === 'start_vm')) {
        if (cloud && !ancestors(startStep.id).has(cloud.id)) {
          fail('start_vm musi zależeć od cloud_init w trybie Proxmox API.');
        }
      }
    }
  } else {
    if (applySteps.length !== 1) fail('Blueprint Terraform/OpenTofu wymaga dokładnie jednego jawnego terraform_apply.');
    const invalidDirect = [...new Set(steps.filter(step => directOnly.has(step.type)).map(step => step.type))];
    if (invalidDirect.length) fail('Terraform/OpenTofu nie może zawierać kroków Direct Proxmox: ' + invalidDirect.join(', ') + '.');
    for (const cloud of steps.filter(step => step.type === 'cloud_init')) {
      if (Object.keys(cloud.conditions || {}).length || Number(cloud.retry || 0) || cloud.rollback) {
        fail('cloud_init w Terraform/OpenTofu jest deklaratywny i nie może mieć conditions, retry ani rollback.');
      }
    }
    if (applySteps.length === 1) {
      const apply = applySteps[0];
      const applyAncestors = ancestors(apply.id);
      if (planSteps.length && !applyAncestors.has(planSteps[0].id)) fail('terraform_plan musi być przodkiem terraform_apply.');
      if (approvalSteps.length) {
        const approval = approvalSteps[0];
        if (!applyAncestors.has(approval.id)) fail('approval musi być przodkiem terraform_apply.');
        if (planSteps.length && !ancestors(approval.id).has(planSteps[0].id)) fail('terraform_plan musi być przodkiem approval.');
      }
      const runtimeTypes = new Set([
        'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
        'run_ansible_playbook', 'register_awx', 'create_snapshot', 'health_check',
      ]);
      for (const step of steps) {
        if (runtimeTypes.has(step.type) && !ancestors(step.id).has(apply.id)) {
          fail(step.type + ' musi zależeć od terraform_apply.');
        }
      }
    }
  }

  const ansibleSteps = steps.filter(step => step.type === 'run_ansible_playbook');
  if (state.ansibleEnabled && ansibleSteps.length !== 1) {
    fail('Skonfigurowane Ansible wymaga dokładnie jednego jawnego kroku run_ansible_playbook.');
  }
  if (!state.ansibleEnabled && ansibleSteps.length) fail('run_ansible_playbook wymaga konfiguracji Ansible.');

  const awxSteps = steps.filter(step => step.type === 'register_awx');
  if (state.awxEnabled && awxSteps.length !== 1) fail('AWX wymaga dokładnie jednego jawnego kroku register_awx.');
  if (!state.awxEnabled && awxSteps.length) fail('register_awx wymaga konfiguracji AWX.');

  if (state.providerType !== 'proxmox') {
    const proxmoxOnly = new Set([
      'cloud_init', 'wait_for_vm', 'wait_for_agent', 'wait_for_ip', 'wait_for_ssh',
      'run_ansible_playbook', 'register_awx', 'create_snapshot', 'health_check',
      'clone_vm', 'configure_vm', 'start_vm', 'set_hostname', 'set_tags',
    ]);
    const invalid = [...new Set(steps.filter(step => proxmoxOnly.has(step.type)).map(step => step.type))];
    if (invalid.length) fail('Te kroki workflow są dostępne tylko dla Proxmox: ' + invalid.join(', ') + '.');
  }

  Object.assign(errors, parts.cloudInit.validate(state));
  return errors;
}


function validateStep(index, state, data, editingItem) {
  const errors = {};
  if (index === 0) {
    if (!state.tenantId || !state.projectId) errors.project_id = 'Wybierz Tenant i Projekt dla Blueprintu.';
    if (!state.name) errors.name = 'Podaj nazwę Blueprintu.';
    if (state.avatarId && !(data.avatars || []).some(item => String(item.id) === String(state.avatarId))) {
      errors.avatar_id = 'Wybrany avatar Blueprintu nie jest już dostępny.';
    }
    if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$/.test(state.slug)) errors.slug = 'Slug musi mieć 1–63 znaków i używać liter, cyfr, _, . lub -.';
  } else if (index === 1) {
    const provider = data.providers.find(value => String(value.id) === String(state.providerId));
    if (!provider) errors.provider = 'Wybierz platformę.';
    if (state.executor === 'proxmox' && provider?.type !== 'proxmox') {
      errors.executor = 'Bezpośredni tryb Proxmox API jest dostępny tylko dla providera Proxmox.';
    }
    if (provider?.type === 'proxmox') {
      if (!state.node) errors.node = 'Wybierz docelowy node.';
      if (!state.selectedTemplateVmid) errors.template = 'Wybierz template/VM bazową.';
      if (!state.providerConnected) errors.provider = state.providerError || 'Nie udało się odczytać zasobów Proxmox.';
    } else if (!state.terraformTemplateId) {
      errors.template = 'Wybierz szablon IaC zgodny z providerem.';
    }
    if (!provider?.credentials_id) errors.provider = 'Provider nie ma przypisanych credentials.';
  } else if (index === 2) {
    if (state.providerType === 'proxmox') {
      if (!Number.isFinite(Number(state.cpu)) || Number(state.cpu) < 1 || Number(state.cpu) > 128) {
        errors.cpu = 'CPU musi mieścić się w zakresie 1–128.';
      }
      if (!Number.isFinite(Number(state.memory)) || Number(state.memory) < 512 || Number(state.memory) > 1048576) {
        errors.memory = 'RAM musi mieścić się w zakresie 512–1048576 MiB.';
      }
      if (!Number.isFinite(Number(state.disk)) || Number(state.disk) < 1 || Number(state.disk) > 65536) {
        errors.disk = 'Dysk musi mieścić się w zakresie 1–65536 GiB.';
      }
      if (String(state.vlanId || '').trim()) {
        const vlan = Number(state.vlanId);
        if (!Number.isInteger(vlan) || vlan < 1 || vlan > 4094) errors.vlan_id = 'VLAN ID musi mieścić się w zakresie 1–4094.';
      }
      if (!state.storage) errors.storage = 'Wybierz storage.';
      if (!state.network) errors.network = 'Wybierz sieć/bridge.';
      if (state.sshPublicKey && (
        !String(state.sshPublicKey).startsWith('ssh-ed25519 ')
        && !String(state.sshPublicKey).startsWith('ssh-rsa ')
        && !String(state.sshPublicKey).startsWith('ecdsa-sha2-')
        || String(state.sshPublicKey).includes('\n')
      )) errors.ssh_public_key = 'Podaj pojedynczy poprawny klucz publiczny SSH.';
      const tags = String(state.tags || '').split(/[,\n]+/).map(value => value.trim().toLowerCase()).filter(Boolean);
      if (tags.length > 20) errors.tags = 'Możesz podać maksymalnie 20 tagów Proxmox.';
      else if (tags.some(tag => !/^[a-z0-9][a-z0-9_.-]{0,63}$/.test(tag))) {
        errors.tags = 'Tagi Proxmox mogą zawierać małe litery, cyfry, kropkę, podkreślenie i myślnik.';
      }
      if (!state.selectEnvironmentOnExecute && !state.environment) errors.environment = 'Wybierz Environment.';
      if (!state.selectApmidOnExecute && !state.apmid) errors.apmid = 'Podaj lub wybierz APMID.';
      else if (!state.selectApmidOnExecute && !/^[A-Z0-9][A-Z0-9_-]{0,62}$/.test(String(state.apmid || '').toUpperCase())) {
        errors.apmid = 'APMID może zawierać litery, cyfry, podkreślenie i myślnik.';
      }
      if (state.selectEnvironmentOnExecute
          && !['test', 'dev', 'nonprod', 'prod'].some(name => data.vmClassification?.environments?.[name] !== false)) {
        errors.select_environment_on_execute = 'Brak włączonych Environment do wyboru podczas tworzenia VM.';
      }
      if (state.selectApmidOnExecute && !(data.vmClassification?.apmids || []).length) {
        errors.select_apmid_on_execute = 'Brak skonfigurowanych APMID do wyboru podczas tworzenia VM.';
      }
    } else {
      const template = data.templates.find(value => value.id === state.terraformTemplateId);
      const required = parts.core.requiredTemplateVariables(template);
      for (const name of required) {
        if (name === 'name') continue;
        const value = state.genericVariables[name];
        if (value === undefined || value === null || value === '') errors['generic_' + name] = 'Pole jest wymagane.';
      }
    }
  } else if (index === 3) {
    Object.assign(errors, parts.hostname.validateHostname(state, data));
  } else if (index === 4) {
    Object.assign(errors, parts.network.validateNetwork(state, data));
  } else if (index === 5 && state.ansibleEnabled) {
    const runs = state.ansibleRuns || [];
    if (!runs.length) errors.ansible_runs = 'Dodaj co najmniej jeden runbook Ansible.';
    runs.forEach((run, runIndex) => {
      const playbook = data.playbooks.find(value => value.id === run.playbook);
      if (!playbook) errors['ansible_playbook_' + runIndex] = 'Runbook #' + (runIndex + 1) + ': wybierz playbook.';
      if (!run.credentials_id) errors['ansible_credentials_' + runIndex] = 'Runbook #' + (runIndex + 1) + ': wybierz credentials.';
      for (const name of playbook?.required_variables || []) {
        if (name === 'hostname' && state.hostnameEnabled) continue;
        if (!run.variables?.[name]) {
          errors['ansible_' + runIndex + '_' + name] = 'Runbook #' + (runIndex + 1) + ': uzupełnij zmienną ' + name + '.';
        }
      }
    });
  } else if (index === 6) {
    Object.assign(errors, validateWorkflow(state, editingItem));
  } else if (index === 7) {
    Object.assign(errors, parts.awx.validate(state));
  }
  state.errors = errors;
  return !Object.keys(errors).length;
}


  parts.validation = Object.freeze({ validateWorkflow, validateStep });
  registerExtension('blueprint-wizard-validation', () => {});
})();
