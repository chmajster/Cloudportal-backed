from datetime import timedelta

from app.blueprint_settings import effective_blueprint_execution_settings
from app.models import JobLog, now


def blueprint_snapshot(job, deployment=None):
    payload = dict((job.payload or {}).get('blueprint') or {})
    if payload:
        return payload
    if deployment is None:
        return {}
    return dict(((deployment.workflow or {}).get('blueprint') or {}))


def blueprint_requires_approval(job, deployment=None):
    return job.operation == 'terraform.apply' and bool(blueprint_snapshot(job, deployment).get('requires_approval'))


def policy_approval_stages(job, deployment=None):
    """Flatten Policy Engine approval effects into deterministic stages."""
    effects = blueprint_snapshot(job, deployment).get('policy_approvals') or []
    stages = []
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        nested = effect.get('stages')
        source = nested if isinstance(nested, list) and nested else [effect]
        for index, stage in enumerate(source):
            if not isinstance(stage, dict):
                continue
            stages.append({
                'name': str(stage.get('name') or effect.get('name') or f'Policy approval {len(stages) + 1}')[:160],
                'approver': stage.get('approver', effect.get('approver')),
                'timeout_hours': stage.get('timeout_hours', effect.get('timeout_hours')),
                'policy_id': str(effect.get('policy_id') or ''),
                'stage_index': index,
            })
    return stages[:20]


def _approval_timeout(config, stage):
    value = stage.get('timeout_hours')
    if value is None:
        return config['approval_timeout_hours']
    try:
        return max(1, min(720, int(value)))
    except (TypeError, ValueError):
        return config['approval_timeout_hours']


def _actor_role_values(actor):
    roles = list(getattr(getattr(actor, 'user', None), 'roles', []) or [])
    names = {str(getattr(role, 'name', '')) for role in roles}
    ids = {str(getattr(role, 'id', '')) for role in roles}
    return names, ids


def _approver_matches(spec, actor, permissions):
    """Evaluate a policy approver without relying on role labels as base RBAC."""
    if spec in (None, '', {}, []):
        return True
    if isinstance(spec, str):
        names, _ = _actor_role_values(actor)
        return spec in names
    if isinstance(spec, list):
        return any(_approver_matches(item, actor, permissions) for item in spec)
    if not isinstance(spec, dict):
        return False

    kind = str(spec.get('type') or 'permission').lower()
    names, ids = _actor_role_values(actor)
    if kind in {'any', 'any_approver'}:
        return True
    if kind == 'user':
        expected_id = spec.get('user_id', spec.get('id'))
        expected_username = spec.get('username')
        if expected_id is not None and str(expected_id) != str(getattr(actor, 'user_id', '')):
            return False
        if expected_username is not None and str(expected_username) != str(getattr(getattr(actor, 'user', None), 'username', '')):
            return False
        return expected_id is not None or expected_username is not None
    if kind in {'role', 'group'}:
        expected_name = spec.get('role') or spec.get('group') or spec.get('name')
        expected_id = spec.get('role_id') or spec.get('id')
        return (
            (expected_name is not None and str(expected_name) in names)
            or (expected_id is not None and str(expected_id) in ids)
        )
    if kind == 'permission':
        expected = spec.get('permission')
        return bool(expected) and str(expected) in set(permissions or ())
    if kind == 'project_admin':
        return bool({'projects.members.manage', 'projects.roles.assign', 'governance.admin'} & set(permissions or ()))
    if kind == 'tenant_admin':
        return bool({'tenants.members.manage', 'tenants.roles.assign', 'governance.admin'} & set(permissions or ()))
    # APMID owner requires owner metadata that the current APMID registry does
    # not model. Failing closed is mandatory; never silently broaden approval.
    if kind == 'apmid_owner':
        return False
    return False


def approve_policy_stage(job, actor, permissions):
    """Approve exactly one Policy Engine stage. Returns None when not policy-driven."""
    payload = dict(job.payload or {})
    state = dict(payload.get('_policy_approval') or {})
    stages = list(state.get('stages') or [])
    if not stages:
        return None

    current_index = max(0, int(state.get('current_stage') or 0))
    if current_index >= len(stages):
        return {'complete': True, 'stage': None}

    stage = dict(stages[current_index])
    if not _approver_matches(stage.get('approver'), actor, permissions):
        from fastapi import HTTPException
        approver_type = (
            str((stage.get('approver') or {}).get('type'))
            if isinstance(stage.get('approver'), dict)
            else 'role'
        )
        message = 'Current user is not an allowed approver for this policy stage'
        if approver_type == 'apmid_owner':
            message = 'APMID owner approval cannot be resolved until an APMID owner is configured'
        raise HTTPException(403, message)

    approvals = list(state.get('approvals') or [])
    approvals.append({
        'stage': current_index,
        'name': stage.get('name'),
        'approved_by': actor.user_id,
        'approved_at': now().isoformat(),
    })
    state['approvals'] = approvals
    state['current_stage'] = current_index + 1
    state['complete'] = state['current_stage'] >= len(stages)
    payload['_policy_approval'] = state
    job.payload = payload
    return {'complete': state['complete'], 'stage': stage, 'next_stage': (
        stages[state['current_stage']] if not state['complete'] else None
    )}


def approval_policy_for_job(db, job, deployment=None):
    payload = dict(job.payload or {})
    persisted = payload.get('_approval_policy')
    if isinstance(persisted, dict):
        try:
            return {
                'auto_approve_for_executors': bool(persisted['auto_approve_for_executors']),
                'approval_timeout_hours': max(1, min(720, int(persisted['approval_timeout_hours']))),
                'auto_approve_source': str(persisted.get('auto_approve_source') or 'global'),
                'approval_timeout_source': str(persisted.get('approval_timeout_source') or 'global'),
            }
        except (KeyError, TypeError, ValueError):
            pass
    project_id = getattr(job, 'project_id', None) or getattr(deployment, 'project_id', None)
    return effective_blueprint_execution_settings(
        db, project_id=project_id, blueprint=blueprint_snapshot(job, deployment)
    )


def gate_job_for_approval(db, job, deployment=None):
    """Apply the effective Global -> Project -> Blueprint policy to Blueprint applies."""
    blueprint = blueprint_snapshot(job, deployment)
    if job.operation != 'terraform.apply' or not blueprint.get('requires_approval'):
        return False

    config = effective_blueprint_execution_settings(
        db,
        project_id=getattr(job, 'project_id', None) or getattr(deployment, 'project_id', None),
        blueprint=blueprint,
    )
    payload = dict(job.payload or {})
    policy_stages = policy_approval_stages(job, deployment)
    if policy_stages:
        # Policy approval is an explicit governance decision and cannot be
        # bypassed by the legacy auto-approve convenience setting.
        config = {
            **config,
            'auto_approve_for_executors': False,
            'auto_approve_source': 'policy',
        }
    payload['_approval_policy'] = {
        'auto_approve_for_executors': config['auto_approve_for_executors'],
        'approval_timeout_hours': config['approval_timeout_hours'],
        'auto_approve_source': config['auto_approve_source'],
        'approval_timeout_source': config['approval_timeout_source'],
    }

    if policy_stages:
        first_stage = policy_stages[0]
        timeout_hours = _approval_timeout(config, first_stage)
        requested_at = now()
        expires_at = requested_at + timedelta(hours=timeout_hours)
        payload['_policy_approval'] = {
            'current_stage': 0,
            'complete': False,
            'stages': policy_stages,
            'approvals': [],
        }
        payload['_approval'] = {
            'status': 'pending',
            'requested_at': requested_at.isoformat(),
            'expires_at': expires_at.isoformat(),
            'stage': 0,
            'stage_name': first_stage.get('name'),
        }
        job.status = 'waiting_approval'
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.status = 'waiting_approval'
        job.payload = payload
        db.add(JobLog(job_id=job.id, message=(
            'workflow.approval.policy.pending: '
            f"stage=1/{len(policy_stages)}; name={first_stage.get('name')}; "
            f'expires_at={expires_at.isoformat()}'
        )))
        return True

    if config['auto_approve_for_executors']:
        payload['_approval'] = {
            'status': 'approved', 'approved_by': job.created_by,
            'approved_at': now().isoformat(), 'automatic': True,
        }
        job.payload = payload
        db.add(JobLog(job_id=job.id, message=(
            'workflow.approval.auto: blueprints.execute uprawnia do automatycznej akceptacji; '
            f"policy_source={config['auto_approve_source']}"
        )))
        return True

    payload['_approval'] = {'status': 'pending'}
    has_runtime_approval = any(str(step.get('type')) == 'approval' for step in (blueprint.get('steps') or []))
    if has_runtime_approval:
        db.add(JobLog(job_id=job.id, message='workflow.approval.deferred: approval nastąpi w zadanym kroku DAG'))
    else:
        expires_at = now() + timedelta(hours=config['approval_timeout_hours'])
        payload['_approval'].update({'requested_at': now().isoformat(), 'expires_at': expires_at.isoformat()})
        job.status = 'waiting_approval'
        if deployment is not None and deployment.active_job_id == job.id:
            deployment.status = 'waiting_approval'
        db.add(JobLog(job_id=job.id, message=(
            'workflow.approval.pending: oczekiwanie na zatwierdzenie; '
            f'expires_at={expires_at.isoformat()}; '
            f"policy_source={config['auto_approve_source']}; timeout_source={config['approval_timeout_source']}"
        )))
    job.payload = payload
    return True
