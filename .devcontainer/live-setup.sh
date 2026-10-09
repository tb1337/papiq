#!/usr/bin/env bash
# Prepare the development instance in the devcontainer, once; safe to run again:
#   - .devcontainer/live.env from live.env.example, with a fresh PAPIQ_SECRET_KEY
#   - the database papiq_live (the bucket papiq-live is created by garage-init at every start,
#     the search index by Papiq itself)
# Run from the repository root (the VS Code task "Papiq: Devinstanz einrichten" does).
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
env_file=$here/live.env

if [ -e "$env_file" ]; then
  echo "live.env exists; keeping it"
else
  key=$(openssl rand -base64 32)
  sed "s|^PAPIQ_SECRET_KEY=$|PAPIQ_SECRET_KEY=$key|" "$here/live.env.example" >"$env_file"
  chmod 600 "$env_file"
  echo "live.env written with a new PAPIQ_SECRET_KEY; fill in PAPIQ_ADMIN_USERNAME, PAPIQ_ADMIN_PASSWORD and the Ollama address"
fi

set -a
# shellcheck disable=SC1090
. "$env_file"
set +a

cd "$here/../backend"
uv run python - <<'PY'
import asyncio
import os

import asyncpg


async def main() -> None:
    name = os.environ["PAPIQ_DB_NAME"]
    connection = await asyncpg.connect(
        host=os.environ["PAPIQ_DB_HOST"],
        port=int(os.environ.get("PAPIQ_DB_PORT", "5432")),
        user=os.environ["PAPIQ_DB_USER"],
        password=os.environ["PAPIQ_DB_PASSWORD"],
        database="postgres",
    )
    try:
        exists = await connection.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", name)
        if exists:
            print(f"database {name} exists")
        else:
            await connection.execute(f'CREATE DATABASE "{name}"')
            print(f"database {name} created")
    finally:
        await connection.close()


asyncio.run(main())
PY
echo "Done. Next: the task 'Papiq starten'."
