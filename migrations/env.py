from alembic import context
from app.database import Base, engine
from app import models
from app.day2 import models as day2_models  # noqa: F401 - register Day-2 tables in metadata
from app.tenancy import models as tenancy_models
from app.projects import models as project_models
from app.resource_scope import models as resource_scope_models
from app.quotas import models as quota_models
from app.instance_backup import models as instance_backup_models  # noqa: F401


def run():
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
