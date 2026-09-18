import importlib.util
from pathlib import Path

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'e2e-disaster-restore.py'
SPEC = importlib.util.spec_from_file_location('e2e_disaster_restore', SCRIPT)
drill = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(drill)


def test_target_guard_requires_exact_confirmation():
    with pytest.raises(SystemExit, match='exactly match'):
        drill.target_guard(
            'postgresql://cloudportal:test@db.example/drill_restore',
            'postgresql://cloudportal:test@prod.example/cloudportal',
            'wrong-name',
        )


def test_target_guard_refuses_production_database():
    with pytest.raises(SystemExit, match='production'):
        drill.target_guard(
            'postgresql://cloudportal:test@db.example/cloudportal',
            'postgresql://cloudportal:other@db.example/cloudportal',
            'cloudportal',
        )


def test_target_guard_accepts_isolated_database():
    target = drill.target_guard(
        'postgresql://cloudportal:test@db.example/cloudportal_restore_drill',
        'postgresql://cloudportal:test@db.example/cloudportal',
        'cloudportal_restore_drill',
    )
    assert target.database == 'cloudportal_restore_drill'
