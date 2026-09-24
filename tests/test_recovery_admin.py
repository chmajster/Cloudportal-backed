from sqlalchemy import select

from app.database import session
from app.models import Role, User
from app.projects.models import ProjectMembership, ProjectRoleAssignment, ProjectRoleGrant
from app.projects.permissions import DEFAULT_PROJECT_ID
from app.recovery import recover_admin
from app.security.core import verify_password
from app.tenancy.models import TenantMembership, TenantRoleAssignment, TenantRoleGrant


def test_recovery_creates_full_local_admin_in_default_project(system):
    with session() as db:
        result = recover_admin(
            db,
            'recovery-admin',
            'recovery-password-12345',
            'recovery@example.com',
        )
        user = db.get(User, result['user_id'])
        assert result['created'] is True
        assert user.username == 'recovery-admin'
        assert verify_password('recovery-password-12345', user.password_hash)
        assert user.is_active is True
        assert user.is_locked is False
        assert user.must_change_password is False
        assert 'Administrator' in {role.name for role in user.roles}

        project_member = db.get(ProjectMembership, (DEFAULT_PROJECT_ID, user.id))
        assert project_member is not None
        assert project_member.status == 'active'
        tenant_member = db.get(TenantMembership, (project_member.tenant_id, user.id))
        assert tenant_member is not None
        assert tenant_member.status == 'active'

        project_role = db.scalar(select(Role).where(Role.name == 'Project Administrator'))
        project_assignment = db.scalar(select(ProjectRoleAssignment).where(
            ProjectRoleAssignment.project_id == DEFAULT_PROJECT_ID,
            ProjectRoleAssignment.user_id == user.id,
            ProjectRoleAssignment.role_id == project_role.id,
        ))
        assert project_assignment is not None
        assert set(db.scalars(select(ProjectRoleGrant.permission_id).where(
            ProjectRoleGrant.assignment_id == project_assignment.id
        ))) == {permission.id for permission in project_role.permissions}

        tenant_role = db.scalar(select(Role).where(Role.name == 'Tenant Administrator'))
        tenant_assignment = db.scalar(select(TenantRoleAssignment).where(
            TenantRoleAssignment.tenant_id == project_member.tenant_id,
            TenantRoleAssignment.user_id == user.id,
            TenantRoleAssignment.role_id == tenant_role.id,
        ))
        assert tenant_assignment is not None
        assert set(db.scalars(select(TenantRoleGrant.permission_id).where(
            TenantRoleGrant.assignment_id == tenant_assignment.id
        ))) == {permission.id for permission in tenant_role.permissions}


def test_recovery_resets_and_unlocks_existing_local_admin(system):
    with session() as db:
        admin = db.scalar(select(User).where(User.username == 'admin'))
        original_id = admin.id
        admin.is_active = False
        admin.is_locked = True
        admin.failed_login_attempts = 7
        db.commit()

    with session() as db:
        result = recover_admin(db, 'admin', 'replacement-password-12345')
        admin = db.get(User, original_id)
        assert result['created'] is False
        assert result['user_id'] == original_id
        assert admin.is_active is True
        assert admin.is_locked is False
        assert admin.failed_login_attempts == 0
        assert verify_password('replacement-password-12345', admin.password_hash)
        assert not verify_password('admin', admin.password_hash)
        assert 'Administrator' in {role.name for role in admin.roles}
        assert db.get(ProjectMembership, (DEFAULT_PROJECT_ID, admin.id)).status == 'active'
