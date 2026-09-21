# Tenant governance foundation

## Delivery boundary

This domain implements **tenant administration and tenant-scoped authorization**.
The Projects extension is documented in `projects.md`. Neither slice claims
isolation of existing deployments, VMs,
credentials, providers, jobs, IPAM, Blueprints or Catalog. Those existing APIs
still use their existing ownership/global-permission rules. The UI displays this
limitation explicitly. Do not rely on this slice alone as a production
multi-tenant infrastructure security boundary.

This is the first implementation slice in `docs/roadmap/governance.md`.

## Ownership and integration

- Mappings: `app/tenancy/models.py`.
- Bounded input/response contracts: `app/tenancy/schemas.py`.
- Live permission evaluation: `app/tenancy/authorization.py`.
- Transactions and invariants: `app/tenancy/service.py`.
- Thin HTTP boundary: `app/tenancy/routes.py`.
- Automatic registration: `app/modules/feature_tenancy.py`.
- Browser feature/styles: `app/web/features/tenancy.js` and
  `app/web/styles/features/tenancy.css`.

No domain logic is added to `app/main.py`, `app/models.py`, `app/api/schemas.py`,
`app/api/outputs.py`, `app/web/core.js`, `app/web/app.js`, or `index.html`.
The existing permission catalog/seeder is extended in `app/rbac/service.py`.
The existing governance lock is extracted into `app/rbac/locking.py` and remains
re-exported from the original module, preserving all existing callers.

## Data model

`Tenant` has a UUID identity, unique slug, description, status, labels, bounded
JSON metadata, system flag, creator, timestamps and optimistic revision.
`TenantMembership` links a global user identity to one tenant, with its own
active/disabled state and revision. Users can hold independent memberships in
multiple tenants. `TenantRoleAssignment` references the **existing** global Role
catalog and belongs to exactly one membership through a composite foreign key.
`TenantRoleGrant` stores the assignment-time permission ceiling relationally.

Deleting a membership cascades its assignments and ceilings. Deleting a shared
role revokes its scoped assignments through foreign keys. Tenant deletion is a
soft tombstone, requires no memberships, preserves the unique slug and never
removes infrastructure or global user identities. Deletion also rejects
non-deleted Projects. The non-empty check must be extended when resource bindings
are introduced.

## Authorization semantics

Global `UserRole` assignments remain global. New scoped grants are not appended
to the browser identity's global permission set and cannot authorize existing
unscoped APIs. There are no runtime checks for usernames or role names.

For a tenant operation, authorization reads the current token, user and role
permission rows. API token scopes are an upper bound. Revocation, expiry,
account disable/lock, temporary lockout and password-change requirements remain
in force. The actor and target tenant are never accepted from an unverified UI
claim.

Cross-tenant administration requires the explicit **global** permission
`tenants.admin` and the operation's global permission. A global `tenants.read`
alone still requires active membership. Tenant-specific permissions can be
inherited from global roles only inside an active membership, unless the
explicit global crossing permission applies.

Assignable tenant permissions:

```
tenants.read
tenants.update
tenants.members.read
tenants.members.manage
tenants.roles.assign
tenants.audit.read
```

Projects additionally contributes its permission catalog to tenant-scoped
delegation, including tenant-level `projects.create` and `projects.admin`.
Existing assignment-time ceilings still apply; adding these permissions to a
role does not silently expand old delegations.

`tenants.create`, `tenants.delete` and `tenants.admin` are platform-only. A role
containing any non-delegable permission cannot be assigned through the tenant
API. A grant must be a subset of the actor's effective permissions in that
specific tenant. A member with greater scoped permissions cannot be modified
by a less-privileged manager.

Effective scoped permissions are the intersection of:

```
current role permissions
AND assignment-time grant ceiling
AND delegable permission catalog
AND API token scopes (for API tokens)
```

Removing a permission from a shared role revokes it immediately. Adding one does
not silently expand old delegations; an authorized role reassignment is needed.
This keeps role editing from becoming an indirect privilege-escalation path.

### Global user-directory boundary

User identities are global in the current application. Adding a new membership
therefore requires **global `users.read` in addition to tenant membership-manage
permission**. Tenant-only managers can administer existing members and roles but
cannot probe the global directory by guessing user IDs. A future invitation or
eligible-user directory can relax this boundary without opening enumeration.
The UI hides its add-member action without that explicit directory permission.
No global users/roles/create/update permissions are implicitly delegated.

### State and error handling

Unknown and inaccessible tenant UUIDs both return `404 TENANT_NOT_FOUND`.
Collections filter in SQL before `limit`, `offset` and counts. A known tenant
with insufficient permissions returns `403 SCOPED_PERMISSION_REQUIRED`.
Suspended tenants allow member reads but reject member writes; disabled tenants
are invisible to members. Global administrators can recover these states.
Default cannot be renamed, moved to another slug, disabled or deleted.

Writes acquire the **existing global governance row lock first**, then re-read
identity/permissions and lock tenant/member rows. This serializes with existing
role/user administration. Requests waiting for the lock cannot use permissions
revoked before the previous transaction committed. Update/delete operations
require `expected_version`; the ORM also applies a version condition. A stale
client gets `409 VERSION_CONFLICT`. A tenant-only administrator cannot remove
the last active human manager. Global recovery remains available.

No state mutation commits inside the service. The existing `get_db` boundary
commits the mutation, audit and business event together or rolls them all back.

## Migration and compatibility

New migration: `7b31e28f49ac_tenancy_foundation.py`.
Parent: `c4f17b8d62a1`.

It adds four domain tables, their constraints/indexes, an audit-scope index, and
one protected Default tenant:

```
00000000-0000-0000-0000-000000000001
```

No merged migration is edited. `migrations/env.py` imports the domain mappings
for metadata comparison. Existing deployments, encrypted credentials and user
passwords are unchanged. **Default Project and legacy resource backfill are
not part of this migration.** Downgrade removes only these new domain tables and
the new audit index; it consequently discards newly added tenant memberships.
Back up before any destructive downgrade.

The normal permission seed gives the built-in Administrator new permissions,
creates Tenant Administrator / Tenant Viewer role definitions and does not give
Infrastructure Administrator platform tenancy administration. Existing API
tokens keep their original scope ceilings: reissue/adjust a token explicitly
before using newly added tenant APIs. Existing infrastructure APIs continue to
work with their existing scopes.

## HTTP contracts

All routes are under `/api/v1`, documented in OpenAPI. List responses include
`items`, `total`, `limit` and `offset`, with `limit` 1–200. Ordering is explicit.

| Method | Route | Contract |
| --- | --- | --- |
| GET/POST | `/tenants` | Authorized list / global creation |
| GET/PUT/DELETE | `/tenants/{tenant_id}` | Detail / revision-checked update / empty tenant tombstone |
| GET | `/tenants/{tenant_id}/permissions` | Effective permissions in this tenant only |
| GET/POST | `/tenants/{tenant_id}/members` | Member list / directory-authorized assignment |
| PUT/DELETE | `/tenants/{tenant_id}/members/{user_id}` | Revision-checked member state/removal |
| PUT | `/tenants/{tenant_id}/members/{user_id}/roles` | Replace scoped assignments and grant ceilings |
| GET | `/tenants/{tenant_id}/assignable-roles` | SQL-filtered delegable role catalog |
| GET | `/tenants/{tenant_id}/audit` | Tenant and membership history without unrelated tenant data |

POST creation supports the existing UUID `Idempotency-Key` convention.
Authorization is rechecked before a cached response can be returned. PUT bodies
require `expected_version`; DELETE passes it as a query parameter.

Example create body:

```json
{
  "name": "Engineering",
  "slug": "engineering",
  "status": "active",
  "labels": {"department": "engineering"},
  "metadata": {"contact": "platform-team"}
}
```

Example membership assignment (IDs refer to existing global identities/roles):

```json
{"user_id": 42, "role_ids": [7], "status": "active"}
```

## Audit, events and UI

Mutations use the existing `audit()` integration, which emits durable events in
the same transaction. Events include `tenant.created`, `tenant.updated`,
`tenant.deleted`, `tenant.member.added`, `tenant.member.updated`,
`tenant.member.removed`, and `tenant.member.roles.changed`. Membership resource
IDs encode `tenant UUID:user ID` so the target is visible in history. The scoped
audit view excludes IP/token fields and unrelated global audit entries. Event
payloads contain safe operation metadata, not request bodies, labels, metadata
or credentials. This slice records action/actor/target, not a full before/after
history of each role or metadata value.

The Tenanci feature provides server-paginated lists, details, status/forms,
member management, paginated role selection, JSON labels/metadata and audit.
It uses the existing navigable page-surface mechanism rather than browser
`prompt`, `confirm` or `alert`. DOM text rendering is used for user content;
async failures are surfaced and stale list/detail responses are discarded.
No localStorage tenant context is used as an authorization source.

## Verification and continuation

Pure service tests cover foreign UUIDs, SQL-before-pagination, live account and
token checks, membership revocation, grant ceilings, role escalation, status,
Default protection, optimistic revisions, rollback and input bounds. The
migration test upgrades an installation containing a legacy deployment and
checks that data remains unchanged. A Node harness executes list rendering and
stale-response guards. HTTP tests use real authentication, audit/event writes,
OpenAPI and idempotency. PostgreSQL tests check simultaneous member revisions
and a waiting writer after membership revocation; SQLite does not certify locks.

Projects, project memberships, Default Project and validated execution context
are documented in `projects.md`. The next implementation boundary is resource-scope
enforcement across every existing entry point. Only after that gate should quota/placement/lease/policy enforcement
be wired into provisioning and advertised as an isolated private-cloud platform.
