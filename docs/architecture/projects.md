# Projects and validated execution context

## Delivery boundary

Projects extend PR #118's tenant foundation. This slice implements project
identity, membership, bounded scoped role inheritance, administration, a
server-validated project selection and Default/Default membership migration.
It does not attach existing deployments, VMs, credentials, providers, jobs,
IPAM, Blueprints or Catalog to projects. Their existing access behavior remains
unchanged until the resource-scoping integration lands. Selecting a project is
not yet a filter or security boundary for those legacy infrastructure screens.
No quota, placement, lease or policy enforcement is claimed by these APIs.

## Components and data

`app/projects/` owns mappings, permission catalog, authorization, schemas,
services and HTTP routes. `app/modules/feature_projects.py` registers routes
without changing the composition root. Browser views/styles live in
`app/web/features/projects.js` and `app/web/styles/features/projects.css`.

Project identity includes UUID, immutable tenant_id, tenant-unique slug, name,
description, status, labels, bounded metadata, default_environment, creator,
timestamps and optimistic revision. Default Project is system-protected.
Deletion retains an identity tombstone and requires no project memberships;
resource integration must extend the non-empty guard before attaching objects.
A tenant containing a non-deleted project cannot be deleted.

Five tables are added: projects, project_memberships,
project_role_assignments, project_role_grants and user_project_contexts.
Composite foreign keys enforce that a project member belongs to the same tenant
and that role assignments reference that membership. Permission ceilings use
existing Role and Permission rows; no secret or user identity is duplicated.

## Permission semantics

Global, tenant and project assignments remain separate. Project grants do not
become global UserRole permissions and cannot authorize existing unscoped APIs.
A global tenants.admin grant plus the requested operation permission crosses
tenants. Other users need active tenant membership. Inside that tenant,
projects.admin permits parent administration without per-project membership;
it cannot be delegated through a project assignment. Otherwise active project
membership is required. Inactive memberships immediately remove project access.

Project-scoped grants are the intersection of the role's current permissions,
the assignment-time relational ceiling, the project-delegable catalog and API
token scopes. Effective project permissions also include permitted global and
tenant grants, only inside the validated membership hierarchy. Role expansion
never silently expands existing assignments; removal applies immediately.

projects.create and projects.admin are tenant/global capabilities. The project
catalog delegates read, update, delete, select, members.read, members.manage,
roles.assign and audit.read. An assigned role containing any permission outside
the actor's effective project scope is rejected in full. Project Administrator
and Project Viewer are convenience seed roles, not authorization shortcuts.
Existing API tokens are not silently expanded to include new permissions.

SQL visibility predicates are applied before row lookup, count and pagination.
Foreign and absent project IDs return the same PROJECT_NOT_FOUND response.
An explicitly supplied tenant/project pair must match. Tenant-disabled projects
are hidden from scoped members. Suspended tenants/projects reject scoped writes;
explicit parent/platform administrators can recover them as documented in code.
Project status changes require parent administration. Updating memberships or
roles checks expected_version and protects the last active human scoped manager,
with explicit parent recovery. A less privileged manager cannot edit a member
whose direct project grants exceed their own permissions.

## User directory boundary

A project manager can select only active members of the project's own tenant.
The paginated eligible-members API never enumerates global or foreign-tenant
identities. Adding a user to a tenant still requires the existing global
users.read capability. Creating project membership does not grant tenant
membership, global roles or provider access. Tenant membership removal cascades
project membership and assignments; disabling it immediately hides projects.

## Transactions and retries

The existing governance lock is acquired before parent tenant, project and member
locks. Token, identity and membership authorization is rechecked after waiting.
Mutations use caller-owned transactions and existing audit/business events.
POST routes authorize before consulting the existing idempotency cache, including
current role-delegation and eligible-member checks. PUT and DELETE revisions
prevent stale updates. No long-running operation executes inside HTTP handlers.

## Active context contract

GET/PUT/DELETE /api/v1/project-context stores a per-user server-side preference.
PUT requires projects.select and projects.read, a matching tenant/project pair
and expected_version (zero only for a never-created preference). The preference
is not a grant. Every lookup or resolution checks current identity, membership,
permissions and status. A revoked preference returns a controlled error rather
than falling back to another tenant. Clearing a revoked preference remains
possible without access to its old project. Clearing retains a nullable identity
pair and increments the revision, preventing the select-clear-select ABA race.
The database rejects a partially null pair.

The immutable ProjectScope result resolves explicit IDs, then a validated
server preference, then Default/Default. Explicit tenant without project is an
error. Queue submissions must copy the resolved IDs into durable operation data;
workers must never consult a later browser preference. The next integration
slice must apply this contract to all relevant existing request/worker paths.

## Migration and compatibility

New migration 8c42f39a50bd follows 7b31e28f49ac. It creates Default Project
00000000-0000-0000-0000-000000000002 in Default Tenant
00000000-0000-0000-0000-000000000001. Existing user identities receive missing
Default tenant/project memberships without copying or adding global roles.
Existing disabled tenant memberships stay disabled. Global account disable/lock
continues to apply. Repeat upgrade does not duplicate system objects.

Downgrade removes project-domain tables and its audit index. It intentionally
retains Default tenant memberships, which may have been edited after upgrade.
Existing user passwords, provider secrets and infrastructure rows are unchanged.
No merged migration is edited.

## HTTP and browser surface

The domain exposes project collections/details, tenant project collections,
permissions, members, eligible-members, assignable-roles and scoped audit plus
project-context and project-creation-tenants. List responses use items, total,
limit and offset with the shared 200-row API cap. New routes and strict schemas
are registered in OpenAPI. Metadata uses the existing bounded tenant validator.

The Projects view uses navigable page surfaces, SQL-filtered lists, paginated
tenant/member/role pickers, effective permissions, versioned mutations and scoped
audit. The selected project is shown in the view and validated on the server.
No localStorage scope authority, unsafe HTML rendering or browser prompt/confirm
is introduced. Generation guards discard results after navigation/logout.
A cross-application switcher and legacy infrastructure filtering remain pending.

## Verification

Tests are in tests/projects/: unit/service isolation and revocation, composite
foreign keys, safe delegation, context revisions including ABA, migration,
Node browser harness, HTTP authentication/idempotency/OpenAPI/events and real
PostgreSQL concurrent-update/revocation gates. The PostgreSQL tests intentionally
do not claim SQLite as concurrency evidence. Record exact executions in the PR.

Required: module boundary check, all browser node --check, full pytest with
PostgreSQL/Redis, and existing tenant regressions. Full browser/provider acceptance
and resource-isolation/governance scenarios are not certified by these tests.
