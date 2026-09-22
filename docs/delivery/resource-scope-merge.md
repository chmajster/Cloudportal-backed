# Resource-scope integration into current main

User-requested merge through PR #122. The original delivery report describes the
pre-publication snapshot and remains historical, not a current merge/CI status.

## Source snapshots

- Published resource-scope branch: `4f82e03e00887c12a4530d0caceaa68017ffb56b`.
- Current main: `d4b48809073b378de024a2deb4c936dc9b988c68`.
- Preserve both histories and main's newer Day-2 and project-context modules.

## Integration corrections

- Keep Day-2 default role grants AND exclude governance administration from the
  Infrastructure Administrator defaults; do not overwrite customized roles.
- Add merge-only Alembic revision `c864db917f20` for `b752ca806e19` and
  `9d2c4e7a1b60`. Neither published migration is rewritten. A fresh database and
  either previous tip now have one `head` target.
- Historical migration fixtures use a plain SQLAlchemy Session before the
  resource-scope tables exist; retain all migration assertions.
- Persist newly created provider/credential assignments inside their original
  flush transaction so a VM created immediately afterwards can see them.
- Legacy Day-2 routes now use resource-scope authorization, and workers recheck
  scope/membership/assignments before execution and during periodic checks.

## Deliberate Day-2 compatibility boundary

Day-2's older action/history models and native target adapters do not yet supply
complete multi-project isolation. Resource operations reject explicit non-Default
scope and installations containing non-Default deployment/VM/resource records with
`GOVERNED_DAY2_REQUIRED`. This also protects action history and idempotent replay.
Workers reject stale queued operations before the provider, including when a
second project receives resources after queueing. Global Day-2 settings remain
administrative. Default-only installations keep their existing actions.

This is intentionally a fail-closed compatibility gate, not completion of a
multi-project Day-2 pipeline. Do not remove it until native target checks, scoped
history and execution concurrency are implemented and tested. Ongoing native
operations already accepted by a provider cannot be undone by reauthorization.

## Tests and continuation

Added five merge regression cases: explicit foreign context, stale queued Day-2
execution plus history gating, RBAC defaults, and upgrade from each published
migration tip. Existing Day-2 tests exercise creation, execution, replay,
revocation and lock cleanup with fake providers, not live infrastructure.

The final CI results and exact merge commit are recorded in PR #122. Do not treat
older delivery test counts as results for this new merge snapshot. Quota, leases,
placement, expanded policies and the final live Engineering/Production acceptance
scenario remain outside this merge. See the original delivery report for the
remaining subsystem work.
