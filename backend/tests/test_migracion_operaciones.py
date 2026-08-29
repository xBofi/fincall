from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory


def test_operational_schema_migration_is_the_head_revision() -> None:
    root = Path(__file__).parents[1]
    config = Config()
    config.set_main_option("script_location", str(root / "alembic"))
    assert ScriptDirectory.from_config(config).get_current_head() == "0003"
