from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_hostname_generator_uses_selected_project_scope_for_every_request():
    source = (ROOT / 'app' / 'web' / 'features' / 'hostnames.js').read_text()

    assert 'function hostnameApi(path, options = {})' in source
    assert 'globalThis.CPProjectContext?.headers?.() || {}' in source
    assert '...(options.headers || {})' in source

    # The feature must not bypass the scoped wrapper. Without X-Tenant-ID and
    # X-Project-ID resource_scope falls back to Default/Default and a selected
    # non-default project can fail with a generic view-load error.
    body = source[source.index('function generatorPatternTokens'):]
    assert 'api(' not in body

    assert "hostnameApi('/hostname-schemes?limit=200')" in source
    assert "hostnameApi('/hostnames?limit=200')" in source
    assert "hostnameApi('/blueprints?limit=200')" in source
    assert "hostnameApi('/deployments?limit=200')" in source
    assert "hostnameApi('/inventory/resources?limit=200')" in source
    assert "hostnameApi('/hostnames/generate', {" in source


def test_tools_hostname_card_uses_selected_project_scope():
    source = (ROOT / 'app' / 'web' / 'features' / 'tools.js').read_text()

    assert (
        "api('/hostname-schemes?limit=200', "
        "{ headers: globalThis.CPProjectContext?.headers?.() || {} })"
    ) in source
