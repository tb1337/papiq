"""Schema migrations with Alembic: one chain for SQLite and Postgres.

`migrate` brings a database to the newest revision in one transaction. On SQLite, Alembic's
batch mode recreates tables for changes SQLite cannot make in place; foreign keys are switched
off meanwhile (as SQLite requires) and checked before the commit.
"""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, text

from papiq.adapters.outbound.sql.database import Database

SCRIPT_LOCATION = Path(__file__).parent


def alembic_config(connection: Connection | None = None) -> Config:
    """Alembic configuration without an ini file; env.py uses `connection`."""
    config = Config()
    config.set_main_option("script_location", str(SCRIPT_LOCATION))
    # Module names must be valid identifiers (mypy, ruff): v0002_add_rules.py
    config.set_main_option("file_template", "v%%(rev)s_%%(slug)s")
    config.attributes["connection"] = connection
    return config


async def migrate(database: Database, revision: str = "head") -> None:
    """Upgrade the schema to `revision`; a no-op if it is there already. Creates a missing
    SQLite file and its directory."""
    if database.is_sqlite and database.engine.url.database:
        Path(database.engine.url.database).parent.mkdir(parents=True, exist_ok=True)
    async with database.engine.connect() as connection:
        if database.is_sqlite:
            await connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
        try:
            await database.begin_write(connection)
            await connection.run_sync(lambda sync: command.upgrade(alembic_config(sync), revision))
            if database.is_sqlite:
                violations = (await connection.execute(text("PRAGMA foreign_key_check"))).all()
                if violations:
                    raise RuntimeError(f"migration breaks foreign keys: {violations}")
            await connection.commit()
        finally:
            database.end_write()
            if database.is_sqlite:
                await connection.rollback()
                await connection.exec_driver_sql("PRAGMA foreign_keys=ON")
