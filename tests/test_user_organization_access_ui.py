from pathlib import Path


def test_user_organization_access_is_scoped_per_tenant():
    source = Path('app/web/features/identity-organization-access.js').read_text()

    assert "id: 'users-organization-access'" in source
    assert "pattern: /^\\/access\\/users\\/(?<id>\\d+)\\/organizations$/" in source
    assert "membershipForUser(tenantId, userId)" in source
    assert "'/tenants/' + encodeURIComponent(tenantId) + '/permissions'" in source
    assert "'/members?limit=' + PAGE_SIZE + '&offset=' + offset" in source
    assert "'/assignable-roles'" in source
    assert "user_id: Number(user.id)" in source
    assert "role_ids: roleIds" in source
    assert "role_ids: selectedRoleIds(picker)" in source
    assert "expected_version: membership.version" in source
    assert "?expected_version=' + encodeURIComponent(membership.version)" in source
    assert "Konto użytkownika oraz dostępy w innych organizacjach pozostaną bez zmian." in source
    assert "Role zapisane tutaj obowiązują tylko w tej organizacji." in source
    assert "'/users/' + encodeURIComponent(user.id) + '/roles'" not in source


def test_users_view_exposes_global_and_organization_access_separately():
    source = Path('app/web/features/identity.js').read_text()

    assert "button('Role globalne'" in source
    assert "button('Organizacje'" in source
    assert "'/access/users/' + encodeURIComponent(user.id) + '/organizations'" in source
    assert "role globalne oraz role w organizacjach/projektach" in source
