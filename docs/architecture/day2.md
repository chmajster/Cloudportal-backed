# Day-2 integration foundation

The `app.day2` domain is registered by `feature_day2.py` and dispatches through the existing job/RQ queue. Provider operations are not performed in HTTP requests. Its API provides action schemas/capabilities, validate/execute, history, cancel/retry, approvals, bulk requests, resource state/protection and settings. Proxmox is the infrastructure adapter; unsupported operations fail closed. Guest automation uses the existing approved Ansible catalog and credential IDs, not arbitrary shell execution.

## Integration repair of PR #114

The original branch conflicted with the event/tenancy/project additions on main. Registration and RBAC now retain those modules. Migration `9d2c4e7a1b60` follows the incremental context revision `8c42f39a50bd`; no previously merged revision is rewritten. Models are registered in API and migration processes.

Execution rechecks the live account, token scope, resource ownership, action permission, current policy and matching approved action/job identity. Requests and jobs use the existing transaction boundary. Idempotent replays reauthorize access. Bulk children use savepoints so a locked-resource rejection does not leave a queued orphan. Approve/cancel serialize against the worker's job lock. The generic dispatcher reconciles terminated Day-2 jobs into their action status.

A resource lock has a unique resource key. An expired lease is not evidence that the provider stopped: active requests and uncertain outcomes remain locked. After timeout/cancellation/unknown provider outcome, the result records `reconciliation_required` and the provider task identifier when available. The lock is retained, including after its lease expires. Definitive success and pre-execution failures release their own lock. Provider task completion requires an explicit successful exit status.

Device schema patterns accept real Proxmox disk/NIC slots. Partial configuration diffs do not describe absent parameters as deletions. NIC updates preserve unspecified VLAN/firewall/link state, disk creation rejects occupied slots, fractional GiB parsing and SSH-key newline handling are covered by tests. Unsupported quiesced snapshots are rejected during validation.

## Scope and remaining work

This is the API/executor foundation, not the complete originally requested operational product. There is no dedicated routed Day-2 UI yet, no completed provider acceptance campaign and no claim of infrastructure tenant/project isolation, quotas or placement integration. Existing resource access rules are retained. Direct Terraform-owned configuration changes remain policy-gated; state reconciliation is not a replacement for Terraform refresh/plan.

Uncertain provider outcomes currently require administrative investigation of the saved task/resource state before a lock may be released. There is no automatic operator-facing recovery endpoint yet. Never release such a lock merely because its timestamp expired. Guest automation cancellation follows the existing Ansible executor behavior. Only advertised/implemented adapters and operations are available.

Automated integration tests use a real HTTP application and database, with deterministic provider substitutes; a PostgreSQL gate exercises simultaneous resource-lock claims. Migration tests preserve existing context revisions, project rows, passwords and credential ciphertext through upgrade/repeat-upgrade/downgrade. They do not substitute for live Proxmox/browser acceptance.
