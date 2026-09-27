from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BLUEPRINTS = ROOT / 'app' / 'web' / 'features' / 'blueprints.js'
DETAILS = ROOT / 'app' / 'web' / 'features' / 'blueprint-details.js'
STYLES = ROOT / 'app' / 'web' / 'styles' / 'features' / 'blueprints.css'


def test_blueprint_list_exposes_routable_details_link():
    source = BLUEPRINTS.read_text()

    assert 'function blueprintDetailsPath(item, scope = null)' in source
    assert "text: 'Szczegóły'" in source
    assert "href: '#' + path" in source
    assert 'const result = [blueprintDetailsLink(item, selected)];' in source
    assert 'detailsPath: blueprintDetailsPath' in source


def test_blueprint_details_route_preserves_project_scope():
    source = DETAILS.read_text()

    assert "id: 'blueprints-details'" in source
    assert "pattern: /^\\/blueprints\\/(?<id>\\d+)\\/(?<slug>(?!execute$)[^/]+)$/" in source
    assert "'/blueprints/creation-scopes?permission=blueprints.read&limit=200'" in source
    assert "'X-Tenant-ID': String(scope.tenant_id)" in source
    assert "'X-Project-ID': String(scope.project_id)" in source
    assert "api('/blueprints/' + encodeURIComponent(id), { headers: scopeHeaders(scope) })" in source
    assert "surface: false" in source


def test_blueprint_details_page_contains_summary_workflow_and_governance():
    source = DETAILS.read_text()

    for heading in (
        "section('Podsumowanie'",
        "section('Dostęp i RBAC'",
        "section('Zasady wykonania'",
        "section('Provisioning'",
        "section('Workflow'",
        "section('Formularz uruchomienia'",
    ):
        assert heading in source

    assert "fact('Wymaga akceptacji'" in source
    assert "fact('Recovery'" in source
    assert "workflowTable(item)" in source
    assert "variableSchemaTable(item)" in source
    assert "deploymentVariablesTable(item)" in source


def test_blueprint_details_page_has_responsive_domain_styles():
    stylesheet = STYLES.read_text()

    assert '/* Blueprint details page */' in stylesheet
    assert '.blueprint-details-page {' in stylesheet
    assert '.blueprint-details-metrics {' in stylesheet
    assert '.blueprint-details-facts {' in stylesheet
    assert '@media (max-width: 620px)' in stylesheet
