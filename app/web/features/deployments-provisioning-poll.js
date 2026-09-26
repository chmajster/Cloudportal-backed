'use strict';

(() => {
function schedule(deployments, refresh, vms = []) {
  const active = (deployments || []).some(item =>
    item.active_job_id
    || ['waiting_approval', 'queued', 'running', 'cancelling', 'waiting_provider', 'recovery_queued']
      .includes(String(item.status || '')));
  const settlingInventory = (vms || []).some(item =>
    item.provisioning_placeholder
    && item.provisioning_job?.status === 'successful');
  const activeDay2 = (vms || []).some(item =>
    ['REQUESTED', 'WAITING_APPROVAL', 'QUEUED', 'RUNNING', 'CANCEL_REQUESTED']
      .includes(String(item.active_action?.status || '').toUpperCase()));
  if (!active && !settlingInventory && !activeDay2) return null;
  return window.setTimeout(refresh, 2000);
}

registerExtension('deployments-provisioning-poll', () => {
  window.DeploymentProvisioningPoll = Object.freeze({ schedule });
});
})();
