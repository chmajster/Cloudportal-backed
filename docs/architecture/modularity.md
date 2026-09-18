# Modular architecture

The repository is organized to minimize merge conflicts when several developers or coding agents work on separate branches at the same time.

## Rule: features own their files

A normal feature change should not modify the application bootstrap, global UI core, or backend composition root. Work inside the domain that owns the behavior.

Frontend domains live in `app/web/features/*.js`. Shared, domain-neutral browser code lives in `app/web/core.js`; cross-domain presentation registries live in `app/web/shared/*.js`. Feature-specific CSS lives in `app/web/styles/features/*.css`. The small `app/web/app.js` file only wires login/logout, refresh, modal events and boot.

Frontend files are discovered at runtime by `/ui/manifest.json`. Adding a new feature file does not require editing `index.html` or a central JavaScript registry. Each feature registers its own route with `registerView({ ... }, handler)`. Route order comes from the numeric `order` field, not script load order.

Backend composition follows the same pattern. Files named `app/modules/feature_*.py` expose `MODULES = (ModuleSpec(...),)`. `app/modules/registry.py` discovers them automatically. A new backend domain therefore does not require editing `app/main.py`.

## Dependency direction

Use this direction:

`feature route/UI -> domain service -> provider/executor/storage adapter -> infrastructure`

Shared code may be imported by a feature. A feature should not import another feature's route module. If two domains need the same logic, move the smallest stable abstraction to a shared service or registry.

API route files should remain thin: validation, permission dependency, transaction boundary, response mapping. Business logic belongs in `app/<domain>/service.py` or another file inside the domain package.

For new Pydantic inputs used by only one domain, define them next to that domain or in its domain package instead of growing `app/api/schemas.py`. Use the shared schema file only for genuinely cross-domain contracts. The same rule applies to response contracts.

## Files treated as shared hot spots

Changes to these files should be isolated in a dedicated platform/refactor branch, not mixed into an ordinary feature PR:

- `app/main.py`
- `app/web/core.js`
- `app/web/app.js`
- `app/web/styles.css`
- `app/models.py`
- `app/api/schemas.py`
- `app/api/outputs.py`
- existing Alembic migration files

New feature branches should normally add or edit domain-owned files instead.

## Branch workflow

Use one domain per branch, for example:

- `feat/ipam-reservations`
- `feat/proxmox-clone-policy`
- `fix/credentials-tls-errors`
- `refactor/ui-table-runtime`

Rebase the branch on current `main` before merge. Do not reformat unrelated files. Do not combine a core refactor with feature behavior in the same PR.

Database changes get a new migration file; never edit an already merged migration. If two branches create migrations concurrently, resolve only the Alembic head relationship during integration.

## Adding a frontend domain

1. Create `app/web/features/<domain>.js`.
2. Keep all view/form/action functions for that domain in that file.
3. Call `registerView` at the bottom of the file.
4. Add `app/web/styles/features/<domain>.css` only when the feature needs dedicated styles.
5. Add domain tests. Do not edit `index.html`; the manifest discovers the files.
6. Run `python scripts/check-module-boundaries.py` and the normal test suite.

## Adding a backend domain

1. Create a thin router, usually `app/api/<domain>.py`.
2. Put business logic in `app/<domain>/service.py`.
3. Create `app/modules/feature_<domain>.py` exposing one or more `ModuleSpec` objects.
4. Add tests for the service and HTTP contract.
5. Do not edit `app/main.py`; module discovery handles registration.

## Integration contract for agents

Every PR should record:

- owned module/domain,
- shared files touched and why,
- API or database contract changes,
- migration head if applicable,
- tests executed,
- remaining follow-up work.

The CI boundary check intentionally rejects architecture regressions such as putting domain views back into `app.js`, registering backend routers directly in `main.py`, or putting feature CSS back into the global stylesheet.
