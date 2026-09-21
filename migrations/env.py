from alembic import context
from app.database import Base, engine
from app import models
from app.tenancy import models as tenancy_models
from app.projects import models as project_models


def run():
    with engine().connect() as connection:
        context.configure(connection=connection, target_metadata=Base.metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
