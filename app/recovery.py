"""Offline/local administrator recovery used only from the host installer."""
import argparse
import re
import sys
import uuid

from sqlalchemy import delete, or_, select, text, update

from app.database import session
from app.models import Audit, PasswordReset, Role, User, now
from app.projects.models import Project, ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.rbac.service import seed
from app.security.core import password_hasher, revoke_user
from app.tenancy.models import Tenant, TenantMembership, TenantRoleAssignment, TenantRoleGrant


USERNAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,62}$')


def _project(db, reference):
    if reference:
        row = db.get(Project, reference)
        if row is None:
            rows = list(db.scalars(select(Project).where(
                Project.slug == reference,
                Project.deleted_at.is_(None),
            )))
            if len(rows) > 1:
                raise RuntimeError('Project slug is ambiguous; use the project ID')
            row = rows[0] if rows else None
    else:
        row = db.get(Project, DEFAULT_PROJECT_ID)
        if row is None:
            rows = list(db.scalars(select(Project).where(
                Project.status == 'active',
                Project.deleted_at.is_(None),
            ).order_by(Project.created_at, Project.id)))
            row = rows[0] if len(rows) == 1 else None
    if row is None:
        raise RuntimeError('Project not found; pass --project with an active project ID or slug')
    if row.deleted_at is not None or row.status != 'active':
        raise RuntimeError('Recovery requires an active, non-deleted project')
    tenant = db.get(Tenant, row.tenant_id)
    if tenant is None or tenant.deleted_at is not None or tenant.status != 'active':
        raise RuntimeError('Recovery requires an active tenant for the selected project')
    return tenant, row


def _role(db, name):
    role = db.scalar(select(Role).where(Role.name == name))
    if role is None:
        raise RuntimeError(f'Built-in role is missing: {name}')
    return role


def _ensure_tenant_assignment(db, user, tenant, role):
    membership = db.get(TenantMembership, (tenant.id, user.id))
    if membership is None:
        membership = TenantMembership(
            tenant_id=tenant.id, user_id=user.id, status='active', created_by=user.id)
        db.add(membership)
        db.flush()
    else:
        membership.status = 'active'

    assignment = db.scalar(select(TenantRoleAssignment).where(
        TenantRoleAssignment.tenant_id == tenant.id,
        TenantRoleAssignment.user_id == user.id,
        TenantRoleAssignment.role_id == role.id,
    ))
    if assignment is None:
        assignment = TenantRoleAssignment(
            tenant_id=tenant.id, user_id=user.id, role_id=role.id, created_by=user.id)
        db.add(assignment)
        db.flush()
    db.execute(delete(TenantRoleGrant).where(TenantRoleGrant.assignment_id == assignment.id))
    db.add_all(TenantRoleGrant(assignment_id=assignment.id, permission_id=p.id)
               for p in role.permissions)


def _ensure_project_assignment(db, user, project, role):
    membership = db.get(ProjectMembership, (project.id, user.id))
    if membership is None:
        membership = ProjectMembership(
            tenant_id=project.tenant_id, project_id=project.id, user_id=user.id,
            status='active', created_by=user.id)
        db.add(membership)
        db.flush()
    else:
        membership.status = 'active'

    assignment = db.scalar(select(ProjectRoleAssignment).where(
        ProjectRoleAssignment.tenant_id == project.tenant_id,
        ProjectRoleAssignment.project_id == project.id,
        ProjectRoleAssignment.user_id == user.id,
        ProjectRoleAssignment.role_id == role.id,
    ))
    if assignment is None:
        assignment = ProjectRoleAssignment(
            tenant_id=project.tenant_id, project_id=project.id, user_id=user.id,
            role_id=role.id, created_by=user.id)
        db.add(assignment)
        db.flush()
    db.execute(delete(ProjectRoleGrant).where(ProjectRoleGrant.assignment_id == assignment.id))
    db.add_all(ProjectRoleGrant(assignment_id=assignment.id, permission_id=p.id)
               for p in role.permissions)


def recover_admin(db, username, password, email=None, project_reference=None):
    username = username.strip().lower()
    email = email.strip().lower() if email else None
    if not USERNAME_RE.fullmatch(username):
        raise ValueError('Username must contain 1-63 letters, digits, dot, underscore or dash')
    if not 12 <= len(password) <= 256:
        raise ValueError('Password must contain between 12 and 256 characters')
    if email is not None and (len(email) > 254 or '@' not in email or any(ch.isspace() for ch in email)):
        raise ValueError('Invalid email address')

    if db.bind.dialect.name == 'postgresql':
        db.execute(text('SELECT pg_advisory_xact_lock(613040621)'))

    seed(db)
    tenant, project = _project(db, project_reference)
    global_admin = _role(db, 'Administrator')
    tenant_admin = _role(db, 'Tenant Administrator')
    project_admin = _role(db, 'Project Administrator')

    user = db.scalar(select(User).where(User.username == username).with_for_update())
    email_owner = db.scalar(select(User).where(User.email == email).with_for_update()) if email else None
    if email_owner is not None and (user is None or email_owner.id != user.id):
        raise RuntimeError('Recovery email is already used by another account')
    created = user is None
    if user is None:
        user = User(
            username=username,
            email=email or f'{username}@localhost.example',
            password_hash=password_hasher.hash(password),
            auth_source='local',
            is_active=True,
            is_locked=False,
            is_service_account=False,
            must_change_password=False,
            failed_login_attempts=0,
            roles=[global_admin],
        )
        db.add(user)
        db.flush()
    else:
        if user.auth_source != 'local':
            raise RuntimeError('Existing account is managed externally; recovery will not convert LDAP identities')
        if user.is_service_account:
            raise RuntimeError('Recovery will not convert a service account into a human administrator')
        user.password_hash = password_hasher.hash(password)
        user.is_active = True
        user.is_locked = False
        user.locked_until = None
        user.failed_login_attempts = 0
        user.must_change_password = False
        if email:
            user.email = email
        if all(role.id != global_admin.id for role in user.roles):
            user.roles.append(global_admin)
        revoke_user(db, user.id)
        db.execute(update(PasswordReset).where(
            PasswordReset.user_id == user.id,
            PasswordReset.consumed_at.is_(None),
        ).values(consumed_at=now()))

    _ensure_tenant_assignment(db, user, tenant, tenant_admin)
    _ensure_project_assignment(db, user, project, project_admin)
    db.add(Audit(
        user_id=user.id,
        token_id=None,
        ip='local',
        source='installer-recovery',
        action='user.recovery_admin_created' if created else 'user.recovery_admin_reset',
        resource='users',
        resource_id=str(user.id),
        result='success',
        request_id=str(uuid.uuid4()),
    ))
    db.commit()
    return {
        'created': created,
        'user_id': user.id,
        'username': user.username,
        'email': user.email,
        'tenant_id': tenant.id,
        'tenant': tenant.name,
        'project_id': project.id,
        'project': project.name,
    }


def main():
    parser = argparse.ArgumentParser(
        description='Create or recover a local Cloudportal administrator from the host.')
    parser.add_argument('--username', default='recovery-admin')
    parser.add_argument('--email')
    parser.add_argument('--project', help='Active project ID or slug; defaults to the system Default project')
    parser.add_argument('--password-stdin', action='store_true')
    args = parser.parse_args()

    if not args.password_stdin:
        parser.error('Password must be supplied with --password-stdin')
    password = sys.stdin.readline().rstrip('\r\n')
    if not password:
        parser.error('Password stdin is empty')

    with session() as db:
        result = recover_admin(db, args.username, password, args.email, args.project)

    print('====================================================')
    print('Cloudportal administrator recovery completed')
    print('====================================================')
    print('Action: ' + ('created' if result['created'] else 'recovered'))
    print('User ID: ' + str(result['user_id']))
    print('Username: ' + result['username'])
    print('Email: ' + result['email'])
    print('Tenant: ' + result['tenant'] + ' (' + result['tenant_id'] + ')')
    print('Project: ' + result['project'] + ' (' + result['project_id'] + ')')
    print('Roles: Administrator, Tenant Administrator, Project Administrator')
    print('Previous sessions/API tokens for an existing account were revoked.')
    print('====================================================')


if __name__ == '__main__':
    main()
