"""Schema migrations with Alembic: one chain for SQLite and Postgres.

`migrate` brings a database to the newest revision in one transaction. On SQLite, Alembic's
batch mode recreates tables for changes SQLite cannot make in place; foreign keys are switched
off meanwhile (as SQLite requires) and checked before the commit.
"""

from pathlib import Path
from typing import Literal

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util import CommandError
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


# How a database stands against the migrations in this version of the code: `current` (at the
# newest revision), `outdated` (empty or older), `newer` (migrated by a newer version).
SchemaState = Literal["current", "outdated", "newer"]


async def schema_state(database: Database) -> SchemaState:
    """Compare the database's revision with the newest migration. Read-only: a missing SQLite
    file is not created. Raises if the database is unreachable."""
    sqlite_file = database.engine.url.database if database.is_sqlite else None
    if sqlite_file and not Path(sqlite_file).exists():
        return "outdated"
    script = ScriptDirectory.from_config(alembic_config())
    async with database.reading() as connection:
        revision = await connection.run_sync(
            lambda sync: MigrationContext.configure(sync).get_current_revision()
        )
    if revision is None:
        return "outdated"
    if revision == script.get_current_head():
        return "current"
    try:
        script.get_revision(revision)
    except CommandError:
        return "newer"
    return "outdated"


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
