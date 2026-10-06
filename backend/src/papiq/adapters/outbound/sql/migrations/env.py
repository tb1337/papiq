"""Alembic environment. Migrations run through `papiq.adapters.outbound.sql.migrate`, which
passes an open connection; there is no URL or ini file."""

from alembic import context

from papiq.adapters.outbound.sql.tables import metadata

connection = context.config.attributes.get("connection")
if connection is None:
    raise RuntimeError("run migrations with papiq.adapters.outbound.sql.migrate")

context.configure(
    connection=connection,
    target_metadata=metadata,
    render_as_batch=connection.dialect.name == "sqlite",
    compare_type=True,
)
with context.begin_transaction():
    context.run_migrations()
