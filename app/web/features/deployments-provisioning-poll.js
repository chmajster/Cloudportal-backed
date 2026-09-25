'use strict';

(() => {
function schedule(deployments, refresh) {
  const active = (deployments || []).some(item =>
    item.active_job_id
    || ['waiting_approval', 'queued', 'running', 'cancelling', 'waiting_provider', 'recovery_queued']
      .includes(String(item.status || '')));
  if (!active) return null;
  return window.setTimeout(refresh, 2000);
}

registerExtension('deployments-provisioning-poll', () => {
  window.DeploymentProvisioningPoll = Object.freeze({ schedule });
});
})();
