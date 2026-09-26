from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_proxmox_task_modal_is_readable_and_hides_raw_type():
    feature = (ROOT / 'app' / 'web' / 'features' / 'inventory-task-modal.js').read_text()
    inventory = (ROOT / 'app' / 'web' / 'features' / 'inventory.js').read_text()
    stylesheet = (ROOT / 'app' / 'web' / 'styles' / 'features' / 'inventory.css').read_text()

    assert "qmshutdown: 'Bezpieczne wyłączenie VM'" in feature
    assert "task-result-state" in feature
    assert "task-summary-grid" in feature
    assert "summaryCard('Operacja', typeValue)" in feature
    assert "summaryCard('Rozpoczęcie', startedValue)" in feature
    assert "summaryCard('Zakończenie', finishedValue)" in feature
    assert "summaryCard('Czas trwania', durationValue)" in feature
    assert "Szczegóły techniczne" in feature
    assert "Typ Proxmox" in feature
    assert "UPID" in feature
    assert "window.InventoryTaskModal = Object.freeze({ show })" in feature

    assert "window.InventoryTaskModal.show(item, result, title)" in inventory

    assert '.task-result-state {' in stylesheet
    assert '.task-summary-grid {' in stylesheet
    assert '.task-technical-grid {' in stylesheet
    assert '.task-upid {' in stylesheet
