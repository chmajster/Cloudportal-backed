# Projects and scoped execution context

Projects is the next domain after tenant administration (PR #118). This domain
provides real project administration isolation; it does **not by itself** turn
legacy infrastructure APIs into a multi-tenant security boundary. Resource
scoping, quota, placement, lease and policy integration are separate dependent
slices in `docs/roadmap/governance.md`.

## Model and ownership

`app/projects/` owns four tables, bounded Pydantic contracts, live authorization,
transactional services and a thin router. `feature_projects.py` discovers the
router automatically. `projects.js` and `projects.css` own browser behavior.

A Project has UUID, tenant, tenant-unique slug, name, description, status,
default environment, labels, bounded JSON metadata, creation/update timestamps,
creator, system flag, tombstone and optimistic version. A composite unique key
`(tenant_id, id)` supports parent-constrained foreign keys.

Project memberships require an existing membership of the **same tenant**.
Composite foreign keys prevent binding a member of tenant A to a project of
B. Removing a tenant membership cascades project memberships and role ceilings.
Disabling a tenant membership immediately suppresses project access without
removing its administrative history.

Shared Role and Permission rows are reused. ProjectRoleGrant is the immutable
assignment-time upper bound, intersected with live RolePermission rows. A later
role expansion does not silently enlarge an existing delegation. A permission
removal applies immediately. Reassignment requires the current actor to possess
all permissions being delegated in this exact scope.

## Permission inheritance and global administration

Global UserRole rows are never replaced or populated by project membership.
Tenant grants and project grants never appear as global `/auth/me` permissions.
The existing API token scope list remains a ceiling on all these sources.

For ordinary project access, an active tenant membership is mandatory. Then:

- Explicit tenant-scoped project grants apply to all projects in that tenant.
- Project-scoped grants apply only through an active membership of that project.
- Existing global project permissions apply inside an active project membership.
- A global `projects.admin` permission allows crossing project boundaries only
  with the operation's own global permission. No username or role-label check is
  used. Tenant-scoped `projects.admin` never becomes a global bypass.

Ordinary tenant membership alone does not grant visibility of sibling projects.
`projects.create` and `projects.admin` are delegable at tenant level, not project
level. Creating a project does not manufacture an administrator role or new
global privileges for its creator.

Tenant Administrator role definitions gain tenant-wide project administration.
Existing assignment-time ceilings remain unchanged: an authorized administrator
must explicitly reassign a role before an older delegation gains new project
permissions. Infrastructure Administrator does not automatically gain project
administration across tenants.

## Lifecycle, races and errors

Active/suspended/disabled states follow the parent tenant. Suspended scope is
read-only for scoped users. Disabled/tombstoned parents hide children. Explicit
global project administration can recover disabled projects; changing a tenant
still requires separate tenant administration permissions.

All writes acquire the existing governance authorization lock first, re-read
current identity and grants, then lock the project/parent and membership. This
preserves the token revocation lock order. Expected-version checks are required
for updates/deletes and no-op updates consume a revision. Concurrent self-removal
must leave an active human manager, considering project, inherited tenant and
membership-bounded global grants. Explicit global administration provides recovery.

List filtering and counts occur in SQL before pagination. Foreign and absent
project identifiers share `404 PROJECT_NOT_FOUND`. A supplied tenant/project pair
must agree. Permission failures in known scope use `SCOPED_PERMISSION_REQUIRED`;
stale writes use `VERSION_CONFLICT`.

The eligible-member directory exposes only active members of the project's own
tenant, not the global user directory. Guessing a foreign user ID does not reveal
whether that user exists. Role selection is paginated and excludes even one
permission outside the actor's delegable scope.

Tenant deletion now rejects non-deleted projects. Project deletion requires no
memberships and retains a tombstone and slug; resource integration must add its
non-empty guard before scoped infrastructure creation is enabled. No project
administration API deletes provider-side infrastructure.

## Execution context and HTTP contracts

`resolve_context` returns a frozen ExecutionContext with server-verified tenant,
project, actor/token identity and immutable effective permissions. The public
`POST /project-context/resolve` validates the selected pair for project read.
Internal operational consumers must explicitly require the relevant operation
permission and `write=True`; accepting a context response is never a permanent
authorization grant. Re-resolve before a write or worker execution.

The router exposes `/projects`, `/projects/{id}`, `/permissions`, `/members`,
`/members/{user_id}/roles`, `/eligible-members`, `/assignable-roles`, `/audit`,
`/tenants/{tenant_id}/projects`, `/project-context/creation-scopes` and the context
resolver. Collection limit is 1–200 with nonnegative offset. Responses include
`items`, `total`, `limit`, `offset`. Inputs reject extra fields and reuse bounded
metadata validation. APIs and response models appear in OpenAPI.

Idempotent creates reauthorize **before replay**, including role/directory checks.
Audit and durable business events use the existing transaction, not a new broker.
Scoped audit omits IP/token fields and cannot expose foreign project events.

## Default migration and backward compatibility

Migration `9a42d10e63bc`, parent `7b31e28f49ac`, creates the four domain tables and
Default Project `00000000-0000-0000-0000-000000000002` in Default Tenant
`00000000-0000-0000-0000-000000000001`.

Existing users receive Default tenant/project memberships, without any new global
role or project role grants. Existing tenant memberships are retained, including
disabled ones. This prepares legacy resource scoping without moving/deleting
existing deployments, credentials, encrypted secrets or passwords. The Default
project cannot be renamed, disabled, moved or deleted. Downgrade removes only
project domain tables and intentionally retains tenant memberships.

## Verification boundary

Unit tests cover scope inheritance, SQL-filtered pagination, live token ceilings,
role changes, parent/member revocation, protected defaults, frozen context,
composite foreign keys and optimistic revisions. Migration tests preserve legacy
identity/resource data. HTTP tests exercise real auth, idempotency, membership,
audit/events and OpenAPI. PostgreSQL tests prove concurrent manager removal and
revalidation after waiting for a lock. The Node harness tests safe text rendering
and stale-result suppression; it is not a full browser E2E substitute.

Record exact current-head results in the PR. Passing project tests does not prove
that quota, resource/credential/provider isolation, leases or policy enforcement
are complete.
