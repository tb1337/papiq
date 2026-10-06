from typing import Protocol


class Repository(Protocol):
    """Metadata persistence: documents, master data, rules, jobs, processing log.

    First adapter: SQLAlchemy on SQLite or Postgres.
    """
