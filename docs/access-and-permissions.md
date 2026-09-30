# Dostęp i uprawnienia

## Model użytkownika

Publiczny model dostępu to:

```text
KTO + CO MOŻE ROBIĆ + GDZIE
subject + role + scope
```

Hierarchia scope:

```text
Organizacja
└── Projekt
    └── Zasób
```

APMID i ENV są atrybutami ograniczającymi scope. Projekt jest opcjonalny dla APMID/ENV; po jego wskazaniu katalog APMID może być dodatkowo ograniczony przez `Project.allowed_apmids`.

Termin „Tenant” pozostaje wyłącznie wewnętrzną nazwą kompatybilności modeli i istniejących tabel. Nowe publiczne API używa `organization_id`.

## Source of truth

`iam_role_assignments` / `RoleAssignment` jest kanonicznym źródłem enterprise authorization. Evaluator nie dodaje osobno uprawnień z `UserRole`, `TenantRoleAssignment` ani `ProjectRoleAssignment`.

Legacy endpointy pozostają kompatybilne. Ich zapisy są synchronizowane write-through do `RoleAssignment`. Migracja wykonuje restartowalny repair-backfill brakujących mirrorów.

Pipeline:

```text
authenticate
→ resolve principal
→ resolve groups
→ resolve real resource scope
→ RoleAssignment
→ inheritance / permission ceiling
→ explicit DENY
→ Policy Engine
→ approval / constraints
→ final decision
→ audit
```

## Grupy zarządzane

Grupy systemowe mają stabilny `system_key`; logika bezpieczeństwa nie zależy od nazwy wyświetlanej.

- `global.admins`
- `org:<organization_id>:admins`
- `org:<organization_id>:auditors`
- `project:<project_id>:admins`
- `project:<project_id>:operators`
- `project:<project_id>:viewers`
- `apmid:<organization_id>:<APMID>:operators`
- `apmid:<organization_id>:<APMID>:viewers`

Grupa otrzymuje zwykły `RoleAssignment`. Użytkownik nie jest automatycznie dodawany do uprzywilejowanej grupy. Usunięcie członkostwa działa natychmiast, ponieważ evaluator rozwiązuje członkostwo grup przy każdym evaluation.

## APMID

`organization_apmids` jest źródłem prawdy. Klucz `vm_classification:<tenant_id>` jest tylko compatibility fallbackem dla instalacji w trakcie migracji.

Każda Organizacja ma chroniony APMID `LEO`:
- zawsze aktywny,
- `is_system=true`,
- nie można go usunąć ani wyłączyć.

## Publiczne API

Główne endpointy:
- `GET /api/v1/access/subjects`
- `GET /api/v1/access/subjects/{type}/{id}`
- `GET /api/v1/access/subjects/{type}/{id}/effective`
- `GET /api/v1/access/organizations`
- `GET /api/v1/access/organizations/{id}/projects`
- `GET /api/v1/access/organizations/{id}/apmids`
- `GET /api/v1/access/roles`
- `GET /api/v1/access/groups`
- `POST /api/v1/access/bindings`
- `PATCH /api/v1/access/bindings/{id}`
- `DELETE /api/v1/access/bindings/{id}`
- `POST /api/v1/access/evaluate`

Zarządzanie APMID:
- `GET /api/v1/organizations/{organization_id}/apmids`
- `POST /api/v1/organizations/{organization_id}/apmids`
- `PATCH /api/v1/organizations/{organization_id}/apmids/{apmid}`
- `DELETE /api/v1/organizations/{organization_id}/apmids/{apmid}`

## Migracja

Migracja `f1a44d7e9c21_unified_access.py`:
1. dodaje relacyjny katalog APMID;
2. migruje efektywny katalog APMID ze starych Settings;
3. zapewnia LEO;
4. naprawia brakujące mirrory legacy grants do RoleAssignment;
5. tworzy managed groups i ich zwykłe RoleAssignment;
6. nie dodaje automatycznie użytkowników do grup.

Legacy tabele nie są jeszcze usuwane. Pozostają warstwą kompatybilności do czasu migracji wszystkich starych endpointów.
