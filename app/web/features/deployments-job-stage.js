'use strict';

(() => {
function workflowStepLabel(type) {
  const labels = {
    generate_hostname: 'Generowanie nazwy hosta',
    allocate_ip: 'Rezerwacja adresu IP',
    create_vm: 'Tworzenie VM',
    clone_vm: 'Klonowanie VM',
    configure_vm: 'Konfiguracja VM',
    cloud_init: 'Cloud-init',
    start_vm: 'Uruchamianie VM',
    set_hostname: 'Ustawianie hostname',
    set_tags: 'Ustawianie tagów',
    wait_for_vm: 'Oczekiwanie na VM',
    wait_for_agent: 'Oczekiwanie na QEMU Guest Agent',
    wait_for_ip: 'Oczekiwanie na adres IP',
    wait_for_ssh: 'Oczekiwanie na SSH',
    run_ansible_playbook: 'Uruchamianie Ansible',
    create_snapshot: 'Tworzenie snapshotu',
    health_check: 'Kontrola stanu',
    release_ip: 'Zwalnianie adresu IP',
    terraform_plan: 'Terraform plan',
    terraform_apply: 'Terraform apply',
    terraform_destroy: 'Terraform destroy',
    condition: 'Sprawdzenie warunku',
    approval: 'Zatwierdzenie',
    delay: 'Oczekiwanie',
    notification: 'Powiadomienie',
  };
  return labels[type] || String(type || 'Nieznany krok').replaceAll('_', ' ');
}

function jobStageInfo(item) {
  if (item.provider_waiting) return { label: 'Oczekiwanie na Proxmox', detail: null };
  if (item.status === 'queued') return { label: 'Oczekuje w kolejce', detail: null };
  if (item.status === 'waiting_approval') return { label: 'Oczekuje na zatwierdzenie', detail: null };
  if (item.status === 'cancelling') return { label: 'Anulowanie', detail: null };

  const raw = String(item.current_stage || '').trim();
  if (!raw) {
    if (item.status === 'running') return { label: 'Uruchamianie zadania', detail: null };
    return { label: '—', detail: null };
  }

  const exact = {
    'job.running': 'Uruchamianie zadania',
    'terraform.state.restore': 'Przywracanie stanu Terraform',
    'cloud-init.preparing': 'Przygotowanie Cloud-init',
    'terraform.init': 'Inicjalizacja Terraform',
    'terraform.import': 'Import Terraform',
    'terraform.plan': 'Terraform plan',
    'terraform.plan.reuse': 'Użycie zapisanego planu Terraform',
    'terraform.apply': 'Terraform apply',
    'terraform.destroy': 'Terraform destroy',
    'terraform.state.persist': 'Zapisywanie stanu Terraform',
    'inventory.synchronizing': 'Synchronizacja inventory',
    'workflow.terraform_apply': 'Terraform apply w workflow',
    'workflow.wait_for_vm': 'Oczekiwanie na VM',
    'workflow.wait_for_agent': 'Oczekiwanie na QEMU Guest Agent',
    'workflow.wait_for_ip': 'Oczekiwanie na adres IP',
    'workflow.qemu_guest_agent.bootstrap': 'Instalacja QEMU Guest Agent',
    'workflow.guest_credential.bootstrap': 'Konfiguracja konta systemowego VM',
    'workflow.completed': 'Workflow zakończony',
    'recovery.terraform_state.restored': 'Odzyskiwanie stanu Terraform',
  };
  if (exact[raw]) return { label: exact[raw], detail: null };

  if (raw.startsWith('workflow.step.start:') || raw.startsWith('workflow.step.completed:')) {
    const parts = raw.split(':');
    const completed = parts[0] === 'workflow.step.completed';
    return {
      label: (completed ? 'Zakończono: ' : '') + workflowStepLabel(parts[2]),
      detail: parts[1] || null,
    };
  }

  if (raw.startsWith('workflow.rollback.start:') || raw.startsWith('workflow.rollback.completed:')) {
    const parts = raw.split(':');
    const completed = parts[0] === 'workflow.rollback.completed';
    return {
      label: (completed ? 'Rollback zakończony: ' : 'Rollback: ') + workflowStepLabel(parts[3]),
      detail: parts[2] || null,
    };
  }

  if (raw.startsWith('ansible.wait_for_connection:')) {
    return { label: 'Ansible: oczekiwanie na połączenie', detail: raw.slice('ansible.wait_for_connection:'.length) || null };
  }
  if (raw.startsWith('ansible.execution:')) {
    return { label: 'Ansible: wykonywanie playbooka', detail: raw.slice('ansible.execution:'.length) || null };
  }

  return { label: raw, detail: null };
}

function cell(item) {
  const stage = jobStageInfo(item);
  return node('div', {},
    node('span', { text: stage.label }),
    stage.detail ? node('div', { class: 'mono muted', text: stage.detail }) : null);
}

registerExtension('deployments-job-stage', () => {
  window.JobStageUI = Object.freeze({ cell });
});
})();
