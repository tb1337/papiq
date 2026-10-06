"""The configured database: SQLite or Postgres, per `PAPIQ_DB_TYPE`."""

from papiq.adapters.outbound.sql import Database, migrate
from papiq.composition.settings import Settings


def open_database(settings: Settings) -> Database:
    if settings.db_type == "sqlite":
        return Database.sqlite(settings.db_sqlite_path)
    # Settings guarantee these for postgres.
    assert settings.db_host and settings.db_name and settings.db_user and settings.db_password
    return Database.postgres(
        host=settings.db_host,
        port=settings.db_port,
        name=settings.db_name,
        user=settings.db_user,
        password=settings.db_password.get_secret_value(),
    )


async def migrate_database(settings: Settings) -> None:
    """Bring the configured database to the newest schema."""
    database = open_database(settings)
    try:
        await migrate(database)
    finally:
        await database.dispose()
