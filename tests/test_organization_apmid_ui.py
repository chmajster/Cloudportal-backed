from pathlib import Path


def test_apmid_tool_is_scoped_to_organization_and_shows_generated_variants():
    source = Path('app/web/features/tools.js').read_text()

    assert "APMID per organizacja" in source
    assert "loadVisibleTenants()" in source
    assert "'/tenants/' + encodeURIComponent(selectedTenantId) + '/vm-classification'" in source
    assert "scope.permissions.includes('tenants.update')" in source
    assert "value + '.' + String(environment).toUpperCase()" in source
    assert "XD.DEV, XD.PROD i XD.NONPROD" in source
    assert "permission: null" in source
    assert "const IMMUTABLE_APMIDS = new Set(['LEO']);" in source
    assert "editable && !locked" in source


def test_blueprint_wizard_loads_classification_from_selected_tenant():
    source = Path('app/web/features/blueprint-wizard-scope.js').read_text()

    assert "'/blueprints/vm-classification'" in source
    assert "'&permission=' + encodeURIComponent(options.item ? 'blueprints.update' : 'blueprints.create')" in source
    assert "data.vmClassification = vmClassification || data.vmClassification;" in source


def test_runtime_apmid_options_remain_scope_header_aware():
    source = Path('app/web/features/blueprint-runtime-apmid.js').read_text()

    assert "api('/vm-classification/options', { headers: scopeHeaders })" in source

def test_policy_editor_refreshes_apmids_when_organization_changes():
    source = Path('app/web/features/operations.js').read_text()

    assert "scopeOptions?.classifications?.[String(tenantId)]" in source
    assert "async function reloadClassification(preserveSelection = true)" in source
    assert "reloadClassification(false)" in source
    assert "Lista pochodzi z wybranej organizacji." in source

