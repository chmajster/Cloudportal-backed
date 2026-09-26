from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_tools_exposes_clone_vm_to_template_workflow():
    feature = (ROOT / 'app' / 'web' / 'features' / 'proxmox-template-tool.js').read_text()
    tools = (ROOT / 'app' / 'web' / 'features' / 'tools.js').read_text()
    navigation = (ROOT / 'app' / 'web' / 'shared' / 'navigation.js').read_text()

    assert "id: 'proxmox-template-clone'" in feature
    assert "navigationParent: 'tools'" in feature
    assert "label: 'VM → Template'" in feature
    assert "convert_to_template: true" in feature
    assert "full: true" in feature
    assert "Klonuj i utwórz template" in feature
    assert "Oryginalna VM" in feature
    assert "Źródłowa VM nie jest konwertowana" in feature
    assert "waitProxmoxTask" not in feature
    assert "waitForTemplate" not in feature
    assert "result.job?.id" in feature
    assert "navigate('jobs')" in feature
    assert "działa w tle" in feature
    assert "'proxmox-template-clone': '/admin/tools/proxmox-template'" in navigation
    assert "field('Szukaj VM', 'source_vm_filter'" in feature
    assert "sourceVmSearchText(item)" in feature
    assert "sourceSearchInput.addEventListener('input'" in feature
    assert ".sort((left, right) => Number(left.vmid) - Number(right.vmid))" in feature
    assert "Brak VM pasujących do wyszukiwania" in feature
    assert "String(item.vmid)" in feature
    assert "item.name || ('vm-' + item.vmid)" in feature
    assert "proxmox-template-source-grid" in feature

    assert "allowed('vms.clone')" in tools
    assert "allowed('vms.template')" in tools
    assert "allowed('jobs.execute')" in tools
    assert "window.ProxmoxTemplateTool.card()" in tools


def test_clone_to_template_backend_is_durable_and_target_only():
    api = (ROOT / 'app' / 'api' / 'proxmox_management.py').read_text()
    reconcile = (ROOT / 'app' / 'providers' / 'task_reconcile.py').read_text()

    assert 'convert_to_template: bool = False' in api
    assert "data.new_vm_id == int(vmid)" in api
    assert "Automatic clone-to-template conversion requires a full clone" in api
    assert "vms.template required for automatic clone-to-template conversion" in api
    assert "'proxmox.clone_template'" in api
    assert "'vm.clone_to_template_queued'" in api
    assert "'background': True" in api

    assert "item.get('action') != 'clone'" in reconcile
    assert "not item.get('convert_to_template')" in reconcile
    assert "adapter.convert_to_template(target_node, int(target_vm_id))" in reconcile
    assert "action='template'" in reconcile


def test_clone_to_template_picker_layout_is_searchable_and_responsive():
    stylesheet = (ROOT / 'app' / 'web' / 'styles' / 'features' / 'tools.css').read_text()

    assert '.proxmox-template-tool {' in stylesheet
    assert '.proxmox-template-source-grid {' in stylesheet
    assert 'grid-template-columns: minmax(220px, .7fr) minmax(300px, 1.3fr);' in stylesheet
    assert 'select[name="source_vm"]' in stylesheet
    assert '@media (max-width: 820px)' in stylesheet
