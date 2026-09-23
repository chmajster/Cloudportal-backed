from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_backup_ui_is_separate_autodiscovered_feature():
    feature = (ROOT / "app/web/features/instance-backup.js").read_text()
    tools = (ROOT / "app/web/features/tools.js").read_text()
    core = (ROOT / "app/web/core.js").read_text()
    bootstrap = (ROOT / "app/web/app.js").read_text()
    index = (ROOT / "app/web/index.html").read_text()

    assert "id: 'instance-backup'" in feature
    assert "navigate('instance-backup')" in tools
    assert "Utwórz i pobierz backup" in feature
    assert "FormData" in feature
    assert "URL.createObjectURL" in feature
    assert "URL.revokeObjectURL" in feature
    assert "RESTORE" in feature
    assert "instance-backup.js" not in index
    assert "instance-backup" not in core
    assert "instance-backup" not in bootstrap


def test_backup_ui_has_feature_scoped_styles():
    css = (ROOT / "app/web/styles/features/instance-backup.css").read_text()
    assert ".instance-backup-layout" in css
    assert ".instance-backup-drop" in css
    assert ".instance-backup-stages" in css
