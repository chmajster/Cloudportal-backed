"""Canonical Blueprint workflow graph helpers.

Blueprint workflow order is determined by dependencies, never by the JSON array
position. The same ordering is used by workers and API progress reporting.
"""


class WorkflowGraphError(ValueError):
    pass


def ordered_workflow_steps(steps):
    rows = [dict(step or {}) for step in (steps or [])]
    if not rows:
        return []

    ids = [str(row.get("id") or "") for row in rows]
    if any(not value for value in ids) or len(ids) != len(set(ids)):
        raise WorkflowGraphError("Blueprint workflow contains missing or duplicate step IDs")

    by_id = {str(row["id"]): row for row in rows}
    known = set(by_id)
    for row in rows:
        step_id = str(row["id"])
        dependencies = [str(value) for value in (row.get("depends_on") or [])]
        if step_id in dependencies or set(dependencies) - known:
            raise WorkflowGraphError("Blueprint workflow contains an invalid dependency")

    pending = list(rows)
    completed = set()
    ordered = []
    while pending:
        ready = [
            row for row in pending
            if {str(value) for value in (row.get("depends_on") or [])} <= completed
        ]
        if not ready:
            raise WorkflowGraphError("Blueprint workflow contains a dependency cycle")
        for row in ready:
            ordered.append(row)
            completed.add(str(row["id"]))
            pending.remove(row)
    return ordered
