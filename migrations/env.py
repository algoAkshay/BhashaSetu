from alembic import context
from sqlalchemy import create_engine

from backend.config import database_url
from backend.models import Base

config = context.config
target_metadata = Base.metadata


def run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(url=database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
elif config.attributes.get("connection") is not None:
    # Explicit connection injection is used by isolated and PostgreSQL integration tests.
    run_migrations(config.attributes["connection"])
else:
    engine = create_engine(database_url(), hide_parameters=True, connect_args={"connect_timeout": 5})
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
