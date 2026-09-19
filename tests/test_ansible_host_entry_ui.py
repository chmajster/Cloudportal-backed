from pathlib import Path


def test_ansible_host_entry_generator_ui_is_present():
    script = Path('app/web/features/tools.js').read_text(encoding='utf-8')
    stylesheet = Path('app/web/styles/features/tools.css').read_text(encoding='utf-8')

    assert "id: 'ansible-host-entry'" in script
    assert "permission: 'ansible.read'" in script
    assert 'Generator wpisu hosta' in script
    assert 'function ansibleHostEntryModel(' in script
    assert 'function ansibleHostIni(' in script
    assert 'function ansibleHostYaml(' in script
    assert 'function parseAnsibleHostVariables(' in script
    assert 'ansible_connection=ssh' in script
    assert 'ansible_connection=winrm' in script
    assert 'ansible_winrm_transport' in script
    assert 'ansible_ssh_private_key_file' in script
    assert 'ansible_become=true' in script
    assert "button('Kopiuj wpis'" in script
    assert "value: 'ini'" in script
    assert "value: 'yaml'" in script
    assert '.ansible-entry-layout' in stylesheet
    assert '.ansible-entry-code' in stylesheet
