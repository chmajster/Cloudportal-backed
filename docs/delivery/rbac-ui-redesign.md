# RBAC access UI redesign

## Status

Phase 1 is implemented on branch `feat/rbac-access-ui-redesign`.

This change deliberately replaces the old "role table + permission checkbox modal"
mental model. Backward-compatible UI behavior is not a goal. Existing authorization
APIs remain authoritative until the later backend consolidation described below.

The new UI is a workspace built around:

```
WHO -> ROLE -> SCOPE -> POLICY -> EFFECTIVE ACCESS
```

## Implemented in Phase 1

The `Role i dostęp` view now contains five tabs:

1. **Przegląd** - access model, quick actions, role summary and catalog counters.
2. **Przypisania** - select a user/service account and manage global roles inline.
3. **Role** - role cards, permission preview, editing and deletion.
4. **Analiza dostępu** - explain whether a global role grants one permission and which
   assigned role is the source.
5. **Zakresy i polityki** - navigation to Tenant, Project and Policy Engine governance.

The user list now exposes **Dostęp** instead of the old role-assignment modal. Opening
it selects the user in the central access workspace.

The role editor has been rebuilt around permission groups and includes:

- permission search/filter,
- selected permission count,
- read-only preset,
- clear selection,
- explicit separation between role definition and scope.

The current implementation reuses the established API authorization checks. The UI
does not become an authorization boundary.


## Phase 1b - simple Dostępy UI

The same simplification was applied to the credentials area. The navigation label is now **Dostępy** and the primary screen no longer starts with a dense technical table.

Implemented behavior:

- credential/access cards instead of the primary table,
- simple health state: **Gotowy**, **Wymaga konfiguracji**, **Wymaga rotacji**, **Wygasł**,
- search by name/type/endpoint/user,
- type filter,
- small overview counters,
- empty-state onboarding with **Dodaj pierwszy dostęp**,
- simplified create/edit form,
- visual type picker with common targets first: Proxmox, SSH, AWX, VMware,
- less common providers under **Pozostałe typy**,
- expiry and rotation dates moved under **Opcje zaawansowane**,
- existing backend test/bootstrap flows for Proxmox, AWX and SSH are preserved.

The form still uses the existing credential API contract and encryption model. No secret is read back into the browser. The simplification is presentation-only; it does not weaken backend validation or secret handling.


## Phase 1c - simple Dostępy inside Blueprint creation

The Blueprint wizard now uses the same simplified access model as the standalone **Dostępy** workspace.

Implemented:

- saved SSH accesses are selected with cards instead of long credential selects,
- the VM step separates:
  - **Dostęp do konta istniejącego w template**,
  - **Dostęp zarządzany przez Cloud-init**,
- the Cloud-init editor uses the same access cards and stores only the selected access ID,
- provider cards use the user-facing term **Dostęp** instead of Credentials,
- the final **Dostęp i bezpieczeństwo** step was simplified:
  - visibility uses three readable toggle cards,
  - allowed roles use checkbox cards instead of a dual-list,
  - allowed users use checkbox cards instead of a dual-list,
  - manager roles and recovery behavior are under **Zaawansowane zarządzanie**,
  - approval remains a first-class launch rule,
- existing hidden/scoped role assignments that are outside the currently visible scope are preserved when saving.

No secret is exposed to the browser. Cloud-init, SSH bootstrap and runtime authorization continue to use the existing backend contracts.

## Source of truth

| Concern | Current source of truth | UI entry point |
| --- | --- | --- |
| Global role definitions | `/api/v1/roles`, `/api/v1/permissions` | Role i dostęp -> Role |
| Global user-role assignments | `/api/v1/users/{id}/roles` | Role i dostęp -> Przypisania |
| Tenant membership / scoped roles | tenancy domain/API | Tenanty |
| Project membership / scoped roles | projects domain/API | Projekty |
| Conditional ABAC / DENY / approvals | Policy Engine | Policy Engine |
| Runtime authorization | backend authorization services | every protected API call |

Do not duplicate authorization rules in the browser. UI visibility is only a usability
feature; the backend must continue to validate every operation.

## Important limitation of Phase 1

The **Analiza dostępu** tab explains only the global RBAC part of the decision.

It intentionally displays a warning that the final decision for a resource can still
be constrained by:

- tenant membership,
- project membership,
- resource scope,
- Policy Engine.

It must not be presented as a complete resource authorization simulator until the
backend exposes one consolidated decision endpoint.

## Target scope model

The target hierarchy for the next iteration is:

```
Organization
  -> Project
    -> APMID
      -> Environment
        -> Resource
```

A role answers **what can be done**.

An assignment answers:

```
subject + role + scope
```

Policy Engine answers **under which conditions the operation is allowed or denied**.

Do not move Organization/Project/APMID/Environment into the role definition. Roles
should remain reusable.

## Phase 2 - unified access read model

Introduce a dedicated access-control read API rather than making the browser join
several domains.

Recommended endpoints:

```
GET /api/v1/access/subjects
GET /api/v1/access/subjects/{subject_id}
GET /api/v1/access/subjects/{subject_id}/assignments
GET /api/v1/access/roles
GET /api/v1/access/scopes
POST /api/v1/access/evaluate
```

Suggested assignment response:

```json
{
  "id": "assignment-id",
  "subject": {
    "type": "user",
    "id": "123",
    "name": "chris"
  },
  "role": {
    "id": 7,
    "name": "VM Operator"
  },
  "scope": {
    "organization_id": "infra",
    "project_id": "LEO-131",
    "apmids": ["IAASTEAM"],
    "environments": ["dev", "test"],
    "resource_ids": []
  },
  "source": "direct",
  "inherited": false
}
```

Suggested evaluation request:

```json
{
  "subject_id": "123",
  "permission": "vms.delete",
  "scope": {
    "organization_id": "infra",
    "project_id": "LEO-131",
    "apmid": "IAASTEAM",
    "environment": "prod",
    "resource_id": "vm-id"
  }
}
```

Suggested evaluation response:

```json
{
  "decision": "deny",
  "permission": "vms.delete",
  "reasons": [
    {
      "type": "role",
      "effect": "allow",
      "name": "VM Administrator",
      "assignment_id": "..."
    },
    {
      "type": "policy",
      "effect": "deny",
      "name": "Production protection",
      "policy_id": "..."
    }
  ]
}
```

The evaluation endpoint should use the same authorization engine as runtime operations.
Do not build a second evaluator only for the UI.

## Phase 3 - unified assignment editor

Replace the separate global, tenant and project assignment flows with one wizard:

1. **Kto** - user, service account, later LDAP/local group.
2. **Rola** - reusable role definition.
3. **Gdzie** - Scope Builder.
4. **Ograniczenia** - relevant policy/condition summary.
5. **Podsumowanie** - effective permissions before save.

Expected Scope Builder behavior:

- organization selection filters projects,
- project selection filters/limits APMID and environments,
- multiple environments can be selected in one assignment,
- inherited vs direct access is visually distinct,
- the resulting scope is shown as a breadcrumb/tree,
- invalid combinations are rejected by backend validation.

If the new assignment schema requires a database change, replace the previous
assignment model rather than adding a compatibility layer solely for the old UI.

## Phase 4 - full access analysis

Extend **Analiza dostępu** into a resource-aware authorization debugger.

It should show:

- effective ALLOW/DENY,
- direct role grants,
- inherited tenant/project grants,
- matching scope,
- Policy Engine matches,
- explicit DENY reason,
- approval requirement,
- actor/subject context,
- resource context,
- decision/request correlation ID.

This should become the primary diagnostic tool for "why can/can't this user do X?".

## Later UX improvements

The backend currently lacks enough metadata for some planned UI behavior. Add it at
the model/API level rather than hard-coding names in the frontend:

- role description,
- role type: system/custom,
- immutable/system role marker,
- role usage count,
- LDAP/local group subjects and group assignments,
- assignment source: direct/group/inherited/system,
- assignment created_by / created_at,
- assignment change history,
- unified scoped assignment identifier.

System roles should eventually expose clone instead of edit/delete where appropriate.

## Files changed in Phase 1

- `app/web/features/identity.js`
  - access workspace and navigation,
  - assignments,
  - role cards/editor,
  - global RBAC analyzer,
  - governance links.
- `app/web/styles/features/identity.css`
  - responsive access workspace layout.
- `app/web/features/credentials.js`
  - simplified Dostępy cards, filters and create/edit flow.
- `app/web/styles/features/credentials.css`
  - responsive simple-access layout.
- `app/web/features/blueprint-wizard-ui.js`
  - reusable Dostępy cards, multi-card ACL and toggle components.
- `app/web/features/blueprint-wizard.js`
  - simplified VM access and Blueprint ACL step.
- `app/web/features/blueprint-wizard-cloud-init.js`
  - saved Dostępy card picker for guest configuration.
- `app/web/styles/features/blueprints.css`
  - Blueprint access-card layout.
- `tests/test_web_console.py`
  - smoke assertions for the new UI contract.
- `docs/delivery/rbac-ui-redesign.md`
  - this implementation/handoff document.

## Acceptance criteria for Phase 1

- Role i dostęp opens without the old role-only table as the primary screen.
- A user can be selected and global roles can be changed without opening the old
  assignment modal.
- Existing backend anti-escalation still applies to role creation/editing/assignment.
- Role permissions are grouped and searchable.
- The global access analyzer identifies roles that grant the selected permission.
- The analyzer clearly distinguishes global RBAC from complete resource authorization.
- Tenant/Project/Policy Engine remain reachable from the workspace.
- Layout remains usable below 760 px.
- Existing role, user, token and backend RBAC APIs continue to be enforced.
- No compatibility wrapper for the previous UI is introduced.

## Known non-goals

Phase 1 does not implement:

- LDAP group assignment,
- one persisted binding spanning Organization/Project/APMID/ENV,
- a cross-domain assignment list,
- resource-level Policy Engine evaluation from the access tab,
- role descriptions/types because the current role API does not expose them,
- migration of tenant/project role storage into a new shared table.

These are intentionally documented as follow-up architecture work rather than hidden
TODO/FIXME placeholders in production code.
