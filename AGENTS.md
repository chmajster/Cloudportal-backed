# Parallel development instructions

Work on one domain per branch. Prefer adding files inside the owning module over editing shared composition files.

Before editing, map the task to one of these frontend domains: identity, credentials, providers, catalog, blueprints, ipam, inventory, deployments/jobs, operations, monitoring/dashboard. Put browser behavior in `app/web/features/<domain>.js` and domain CSS in `app/web/styles/features/<domain>.css`.

Do not add domain logic to `app/web/app.js`; it is bootstrap-only. Do not add domain views to `app/web/core.js`. New feature scripts and feature styles are discovered automatically, so do not edit `app/web/index.html` just to register a feature.

For backend work, keep HTTP routing thin and put business logic in the domain package. Register a new backend domain by adding `app/modules/feature_<domain>.py`; do not add router imports to `app/main.py`.

Treat `app/main.py`, `app/web/core.js`, `app/web/styles.css`, `app/models.py`, `app/api/schemas.py` and `app/api/outputs.py` as shared hot spots. Modify them only when the change is genuinely cross-cutting. If a schema is used by one domain only, define it in that domain instead of the shared schema file.

Never modify an already merged migration. Keep commits scoped to the module. Avoid repository-wide formatting in feature branches.

Before handing off work, run:
`python scripts/check-module-boundaries.py`
`find app/web -type f -name '*.js' -print0 | xargs -0 -n1 node --check`
`pytest -q --tb=short`

In the PR description, record what is finished, what remains, which shared files were touched, and the exact tests run. This is required so another agent can continue without rediscovering branch state.

See `docs/architecture/modularity.md` for the full architecture rules.
