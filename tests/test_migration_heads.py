from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


ROOT = Path(__file__).resolve().parents[1]


def test_alembic_has_exactly_one_head_after_parallel_feature_migrations():
    config = Config(str(ROOT / 'alembic.ini'))
    heads = ScriptDirectory.from_config(config).get_heads()
    assert len(heads) == 1


def test_merge_revision_joins_blueprint_roles_and_availability_plan_heads():
    config = Config(str(ROOT / 'alembic.ini'))
    script = ScriptDirectory.from_config(config)
    merge = script.get_revision('c13d9a42b5e7')
    assert set(merge._normalized_down_revisions) == {
        '0c4e71a9d2f8',
        'ab91c4e7d260',
    }
