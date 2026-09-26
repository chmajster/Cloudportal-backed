'use strict';

(() => {
  function jobLogVmId(current, deployment = null, logs = []) {
    const values = deployment?.variables || {};
    const direct = current?.vm_id
      ?? values.vm_id
      ?? values.vmid
      ?? values.target_vmid
      ?? values.new_vmid
      ?? null;
    if (direct !== null && direct !== undefined && direct !== '') return String(direct);

    for (const row of logs || []) {
      const message = String(row?.message || '');
      const match = message.match(/\bVMID\s+(\d+)\b/i)
        || message.match(/\bvm[_-]?id\s*[=:]\s*(\d+)\b/i)
        || message.match(/\/qemu\/(\d+)\b/i);
      if (match) return match[1];
    }
    return '';
  }

  function jobLogHostname(deployment = null) {
    const variables = deployment?.variables || {};
    const value = deployment?.name ?? variables.hostname ?? variables.name ?? '';
    return String(value || '').trim();
  }

  function jobLogIdFromRoute() {
    const route = String(location.hash.slice(1) || '').split('/page/')[0].replace(/\/+$/, '');
    const match = route.match(/^\/?jobs\/([^/]+)$/);
    if (!match) throw new Error('Brak ID zadania w adresie URL.');
    try {
      return decodeURIComponent(match[1]);
    } catch {
      throw new Error('ID zadania w adresie URL jest nieprawidłowe.');
    }
  }

  window.DeploymentsJobLogUtils = { jobLogVmId, jobLogHostname, jobLogIdFromRoute };
  registerExtension('deployments-job-log-utils', () => {});
})();
