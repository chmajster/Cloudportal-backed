# Private-cloud governance delivery plan

Baseline: `main` at `f5e716171521069d44e84ef54ec760f99b29c5df` (2026-09-21).

This is a delivery plan, not a claim that multi-tenancy or the complete governance subsystem already exists. The source specification has 76 sections. Existing working domains must be extended, not replaced.

## Architecture findings

- `AGENTS.md` and `docs/architecture/modularity.md` require domain-owned services, thin HTTP routers, automatically discovered `feature_*.py` module registrations and frontend features. Do not manually register routes in `app/main.py` or feature scripts in `index.html`.
- `app/models.py` currently defines global UserRole and RolePermission assignments. `app/security/core.py` computes global permissions and intersects API token scopes. Scoped grants must not be flattened into these global permissions.
- `app/access.py` currently authorizes deployments, jobs and inventory by ownership or global read/manage-all permissions. It is not a tenant isolation boundary. Adding tenant CRUD alone must never be advertised as isolation of these existing APIs.
- `app/api/administration.py` already prevents granting permissions outside the actor's effective set. Scoped grants need the same anti-escalation rule in the target scope.
- `app/api/common.py` uses database-side offset/limit pagination, with a maximum limit of 200. Preserve this convention.
- Existing CI runs PostgreSQL/Redis integration tests, module-boundary checks and syntax checks for every browser JavaScript file. New tests must augment, not replace, these checks.

## Delivery slices and measurable gates

| Slice | Domain and deliverable | Merge/acceptance gate |
| --- | --- | --- |
| 01 | Tenancy: tenant lifecycle, membership, scoped authorization primitives and Default tenant | Tenant IDOR, inactive membership, API-token ceiling, no role-name checks, anti-escalation and migration tests |
| 02 | Projects: projects, memberships, explicit Tenant/Project context and Default project | Project isolation, mismatched tenant/project rejection, deterministic defaults, permission inheritance tests |
| 03 | Resource scoping: safe backfill of deployments, managed resources, VMs, jobs and user-owned associations | Existing installation upgrade, default provisioning, filtered collections, direct-ID access, worker/event payload scope tests |
| 04 | Quotas: dimensions, hierarchical limits, ledger and atomic reservations | PostgreSQL concurrency: limit 10, used 9, 10 concurrent requests, exactly one reservation; idempotent commit/release |
| 05 | Quota recovery: positive Day-2 deltas, provider-confirmed release and reconciliation | Worker crash, timeout, partial provisioning, cancellation and duplicate callback tests |
| 06 | Leases: defaults/maxima, extensions, approvals, expiry worker and jobs | Extension/expiry race, notification deduplication, approved expiry actions and retry-safe scheduling |
| 07 | Placement: capability/capacity snapshots, hard filters, weighted scoring and simulation | Provider-neutral adapters, deterministic tie-breaks, affinity, stale-capacity rejection and concurrent capacity reservations |
| 08 | Policy core: declarative schema, phases/effects, bounded evaluation, immutable inputs and versioned decisions | DENY/approval monotonicity, mutation whitelist, safe regex, deterministic inheritance and dry-run with no changes |
| 09 | Policy integration: provisioning, Blueprint, Catalog, Day-2, leases and obligations | No execution path bypasses authorization/policy/quota; approval resumption revalidates context; obligations use existing jobs/events |
| 10 | Governance UI: tenant/project administration and switcher, quota/lease/placement/policy views | Backend remains authoritative, navigable pages, Visual/YAML validation, explain and permission-aware visibility |
| 11 | Audit/events/monitoring: existing broker integration and bounded-cardinality metrics | Atomic event persistence, redaction, retention and scoped decision-history tests |
| 12 | Production acceptance and documentation | Engineering/Production scenario, denied 8-to-32 CPU resize, approved destroy, quota release only after confirmed deletion |

Use a separate domain branch for each independently reviewable slice. Do not advance an incomplete slice by hiding missing enforcement behind successful CRUD tests. Document dependencies between PRs. All required database changes use new migrations; never rewrite a merged migration.

## Security and consistency decisions that must survive all slices

1. Tenant is the isolation boundary; project is the execution context. A client-supplied UUID is not evidence of access.
2. Global, tenant and project assignments remain distinct. Explicit global administrative permissions may cross scope; tenant/project grants never become global powers.
3. API token scopes are a ceiling, including for scoped permissions. Authentication state, account revocation and membership must be rechecked.
4. Scope filtering happens in SQL before pagination. Object endpoints and nested references require equivalent checks. Do not disclose another tenant's names, UUIDs, membership or reasons through explain output.
5. A scoped administrator cannot grant privileges they do not effectively possess in that scope, even by assigning an existing global administrative role.
6. Authorization and membership mutations require transaction-safe serialization and auditable results; role changes must not silently enlarge a previously delegated grant.
7. Quota/capacity reservation uses PostgreSQL transactions and a deterministic lock order, not read-then-create checks. Quota and capacity are distinct constraints.
8. Provider timeouts do not prove a resource was never created. Reconcile before releasing reservations for uncertain or partial operations.
9. Approval waits must not hold database transactions or indefinite capacity reservations. Resume through existing jobs, reauthorize and revalidate current quota/capacity/policy.
10. Placement scoring precedes winner selection. Recheck hard constraints, quota deltas and capacity after any permitted request mutation.
11. Policy contexts contain no decrypted secrets. Mutations create a validated request revision, not modifications to actor/scope/audit context. Decisions retain exact policy versions.
12. Lower-precedence ALLOW cannot remove DENY or required approvals. DRY_RUN/simulate must not mutate resources, reservations, obligations or approvals.
13. Expiry-triggered destructive actions use the existing jobs, locks, idempotency and audit mechanisms, never direct scheduler provider calls.
14. Existing provisioning continues in Default/Default until explicit scoped integration is complete. A migration must not silently move or delete user data.

## Required verification

```sh
python scripts/check-module-boundaries.py
find app/web -type f -name '*.js' -print0 | xargs -0 -n1 node --check
pytest -q --tb=short
```

Run each new domain's unit, API, migration and PostgreSQL concurrency tests as applicable. Record actual results and distinguish pre-existing failures from regressions. Provider-side acceptance requires a real configured test provider; unit tests alone cannot certify production readiness.

## Current implementation status

Implemented on `feat/tenancy-governance-foundation` / PR #118:

- Domain-owned Tenant, membership, scoped assignments and relational permission ceilings.
- Tenant administration, SQL-filtered lists, scope permissions, membership/role administration and scoped audit APIs.
- Protected Default tenant migration `7b31e28f49ac`, based on `c4f17b8d62a1`; legacy resource data unchanged.
- Existing authentication, global RBAC, token ceilings, shared governance lock and audit/event broker reused.
- Tenants browser feature with navigable forms, member/role views, pagination and audit.
- Unit, migration, HTTP, Node UI and PostgreSQL concurrency tests; actual execution results belong in the PR.

**Projects extension:** domain identity, scoped memberships/role inheritance,
Default/Default membership migration, validated server-side selection, project
administration/eligible-user APIs and UI, migration/HTTP/concurrency tests.
See `docs/architecture/projects.md`. Selecting a project does not yet scope the
legacy infrastructure APIs.

**Not yet implemented:** legacy resource backfill and full resource isolation,
cross-application project switcher, quota/usage/reservations, leases/workers, placement/capacity reservation,
expanded policy engine/simulation, provisioning/Blueprint/Catalog/Day-2 integration and complete production acceptance.
No quota/placement/lease/policy enforcement is claimed by the tenancy CRUD endpoints.

Next: verify the Projects extension in full CI, then deliver a cross-cutting
resource-scoping integration PR.
Read `docs/architecture/tenancy.md` before extending authorization. Keep tenant grants out of the
existing global permission set, preserve API token ceilings, and extend the tenant deletion non-empty guard.
New tenant membership assignment requires global user-directory read permission until a safe invitation
mechanism exists. Project membership uses the scoped eligible-member directory. Do not remove that check to enable user-ID enumeration.
