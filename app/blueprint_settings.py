from __future__ import annotations

from app.models import Setting

SETTING_KEY = 'blueprint_execution'
DEFAULTS = {'auto_approve_for_executors': True, 'approval_timeout_hours': 48}


def blueprint_execution_settings(db):
    row = db.get(Setting, SETTING_KEY)
    raw = dict(row.value) if row and isinstance(row.value, dict) else {}
    return {
        'auto_approve_for_executors': bool(raw.get('auto_approve_for_executors', DEFAULTS['auto_approve_for_executors'])),
        'approval_timeout_hours': max(1, min(720, int(raw.get('approval_timeout_hours', DEFAULTS['approval_timeout_hours']) or 48))),
    }


def _value(source, name):
    if source is None:
        return None
    return source.get(name) if isinstance(source, dict) else getattr(source, name, None)


def effective_blueprint_execution_settings(db, *, project_id=None, blueprint=None):
    """Resolve Global -> Project -> Blueprint approval policy precedence."""
    result = dict(blueprint_execution_settings(db))
    result['auto_approve_source'] = 'global'
    result['approval_timeout_source'] = 'global'

    project = None
    if project_id:
        from app.projects.models import Project
        project = db.get(Project, str(project_id))
    if project is not None:
        if project.blueprint_auto_approve_for_executors is not None:
            result['auto_approve_for_executors'] = bool(project.blueprint_auto_approve_for_executors)
            result['auto_approve_source'] = 'project'
        if project.blueprint_approval_timeout_hours is not None:
            result['approval_timeout_hours'] = max(1, min(720, int(project.blueprint_approval_timeout_hours)))
            result['approval_timeout_source'] = 'project'

    blueprint_auto = _value(blueprint, 'auto_approve_for_executors')
    blueprint_timeout = _value(blueprint, 'approval_timeout_hours')
    if blueprint_auto is not None:
        result['auto_approve_for_executors'] = bool(blueprint_auto)
        result['auto_approve_source'] = 'blueprint'
    if blueprint_timeout is not None:
        result['approval_timeout_hours'] = max(1, min(720, int(blueprint_timeout)))
        result['approval_timeout_source'] = 'blueprint'
    return result


def save_blueprint_execution_settings(db, data):
    value = {
        'auto_approve_for_executors': bool(data.auto_approve_for_executors),
        'approval_timeout_hours': int(data.approval_timeout_hours),
    }
    row = db.get(Setting, SETTING_KEY)
    if row is None:
        row = Setting(key=SETTING_KEY, value=value)
        db.add(row)
    else:
        row.value = value
    db.flush()
    return value
