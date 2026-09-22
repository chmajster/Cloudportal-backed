# Quota accounting and recovery

This document defines the quota boundary introduced by governance slices 04 and
05. Tenant/project resource ownership is provided by the earlier resource-scope
slice; quota does not replace authorization, provider capacity or placement.

## Dimensions and hierarchy

The backend accounts four normalized dimensions:

- `vm_count`: number of governed virtual machines;
- `vcpu`: requested virtual CPUs;
- `memory_mb`: requested memory in MiB;
- `disk_gib`: requested governed disk capacity in GiB.

A tenant ceiling constrains the aggregate of all projects in the tenant. A
project ceiling constrains only that project. When both exist, both checks must
pass. Absence of a limit means that level is unbounded; usage is still recorded.

`used` is provider-confirmed committed usage. `reserved` is capacity held by
work that has passed admission but is not yet safely committed or released.
Limits cannot be lowered below `used + reserved`.

## Transaction model

Quota admission is PostgreSQL-serialized. Every mutation locks the Tenant row
first and the Project row second, then locks limit/usage/reservation rows. This
fixed order prevents read-then-create races between concurrent requests.

A reservation has a stable request key and one of these states:

- `reserved`: admitted and holding positive capacity;
- `committed`: provider outcome confirmed and usage/allocation updated;
- `released`: provider mutation did not begin or absence/failure was confirmed;
- `uncertain`: provider mutation may have begun but its result is not known.

The same request key cannot account different work. A subject cannot carry two
active/uncertain reservations at once. Commit and release operations are
idempotent.

Positive deltas increase `reserved` immediately. Negative deltas never reduce
`used` before provider confirmation. This means a destroy timeout cannot
artificially create free quota.

## Provisioning and retry

Terraform/OpenTofu apply/import/destroy jobs receive a quota reservation only
when they enter `queued`. Approval waits therefore hold neither a database
transaction nor indefinite quota capacity. Resume reauthorizes through the
existing job pipeline and recreates the reservation atomically.

A Terraform operation is considered provider-submitted only immediately before
the mutating `terraform apply` subprocess. Preflight, init and plan failures
release the reservation and remain retry-safe. Failure/cancellation after apply
submission makes the reservation `uncertain`.

Successful inventory synchronization commits apply/import usage. Successful
destroy commits the negative allocation. Recovery destroy and confirmed absence
remove stale allocations without trusting a timeout as proof of deletion.

## Crash and uncertainty recovery

Persisted Terraform state that independently confirms a deployment exists can
resolve a reserved/uncertain apply after worker failure. Direct provider
inventory reconciliation can likewise resolve Terraform apply/destroy
reservations from confirmed presence/absence.

Resolution is conservative:

- confirmed presence commits an apply and releases a destroy;
- confirmed absence releases an apply and commits a destroy;
- Day-2 resize/disk uncertainty is not inferred from mere VM presence and must
  be reconciled from the actual operation/provider result.

Manual reconciliation remains available for `uncertain` reservations when an
operator has independent evidence unavailable to the automatic paths.

## Day-2 integration

Quota validation runs before a governed Day-2 mutation is queued. Compute and
disk growth reserve positive deltas; delete/destroy deltas are committed only
after provider confirmation. A timeout retains the resource lock and quota
reservation as reconciliation-required.

When limits are active, modifying an unaccounted resource fails closed instead
of manufacturing an incorrect baseline. Clone under active quota is forced
through governed provisioning so the clone receives a distinct allocation.

## API and RBAC

Project-scoped API:

- `GET /api/v1/quotas`
- `PUT /api/v1/quotas/project/{dimension}`
- `GET /api/v1/quotas/reservations`
- `POST /api/v1/quotas/reservations/{id}/reconcile`

Tenant ceiling mutation uses
`PUT /api/v1/quotas/tenant/{dimension}`.

Permissions are `quotas.read`, `quotas.manage` and
`quotas.tenant.manage`. Project roles cannot delegate
`quotas.tenant.manage`. Infrastructure Administrator does not receive that
tenant-wide authority by default; Tenant Administrator and explicit platform
administration retain it.

## Migration

Alembic revision `d3f8c41b72a0` follows the published resource-scope/Day-2
merge head `c864db917f20`. It adds quota limits, usage, reservations,
allocations and ledger tables and backfills confirmed active deployments into
allocation/usage accounting. Published earlier migration identities are not
rewritten.

Downgrade is refused while tenant/project limits or active/uncertain
reservations exist.

## Verification gates

The quota suite covers:

- reservation commit/release and idempotent re-arming after approval;
- uncertain reservations retaining capacity until reconciliation;
- Day-2 denial before provider execution;
- successful Day-2 commit exactly once;
- cancellation before provider execution releasing capacity;
- Day-2 provider timeout retaining capacity as `uncertain`;
- worker-crash inventory recovery committing confirmed persisted state;
- real PostgreSQL concurrency: limit 10, used 9, ten simultaneous `+1`
  requests, exactly one winner.

Placement capacity, leases and the expanded policy engine remain separate
governance domains. Quota must be rechecked after any later policy mutation or
placement decision; those slices must not bypass this admission boundary.
