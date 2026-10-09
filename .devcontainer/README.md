# Papiq in the devcontainer: development and the development instance

The devcontainer (`devcontainer.json`, `compose.yml`) is where Papiq is developed and tested, and
where the **development instance** runs: a Papiq with real documents, started from VS Code tasks,
sharing Postgres, Garage and Meilisearch with development and tests but with its own database,
bucket and search index. Nothing here is reachable from outside the machine; Papiq speaks plain
HTTP on `localhost`.

| Service (`compose.yml`) | Purpose |
| --- | --- |
| `dev` | The workspace: target `dev` of the `Dockerfile` (the system packages of the runtime image, Python 3.13 with uv, Node.js with pnpm, Docling models, Git, GitHub CLI, Claude Code) |
| `postgres` | Databases `papiq` (development), `papiq_test_*` (integration tests, created and dropped per test) and `papiq_live` (the development instance) |
| `garage` | S3 object store; `garage-init` creates the key and the buckets `papiq` (development, tests below `tests/`) and `papiq-live` (the development instance) at every start |
| `meilisearch` | Search; indexes `papiq-documents` (development), `papiq-test-*` (tests) and `papiq-live-documents` (the development instance) |

The credentials in `dev.env` are fixed development values and never leave the machine. Ports
8000 (API), 5173 (Vite), 7700 (Meilisearch) and 3900 (Garage) are forwarded to the host.

## Three sets of data that never touch each other

| Use | Settings | Database | Bucket | Index |
| --- | --- | --- | --- | --- |
| Development (`uv run python -m papiq.composition api`, `pnpm dev`) | `dev.env`, injected into the container | `papiq` | `papiq` | `papiq-documents` |
| Tests (`uv run pytest -m integration`) | `dev.env`; the tests derive their own names | `papiq_test_<random>` | `papiq`, prefix `tests/<random>/` | `papiq-test-<random>` |
| Development instance (the tasks) | `live.env` (not in the repository) | `papiq_live` | `papiq-live` | `papiq-live-documents` |

The tests create and remove their databases, prefixes and indexes themselves. Running
`pytest -m integration` while the development instance is up changes nothing in `papiq_live`,
`papiq-live` or `papiq-live-documents`. The volumes (`postgres-data`, `garage-meta`,
`garage-data`, `meilisearch-data`) outlive rebuilds of the devcontainer; `docker compose down -v`
in `.devcontainer/` deletes them all, including the development instance.

## Set the development instance up

Once, in the devcontainer (VS Code: Terminal → Run Task):

1. **Papiq: Devinstanz einrichten** copies `live.env.example` to `live.env` with a fresh
   `PAPIQ_SECRET_KEY` and creates the database `papiq_live` (the bucket comes from
   `garage-init`, the index from Papiq). It never overwrites an existing `live.env`. In a
   devcontainer that was started before the bucket `papiq-live` existed, run `garage-init`
   once (`docker compose -f .devcontainer/compose.yml run --rm garage-init`, from the container
   or the host) or rebuild the container; `/api/v1/health` says `object_store: failed` until then.
2. Fill in `live.env`: `PAPIQ_ADMIN_USERNAME` and `PAPIQ_ADMIN_PASSWORD` for the first admin
   (use the user name you have in Paperless, so the migration maps the documents to you), the
   address of your Ollama in `PAPIQ_LLM_BASE_URL` and `PAPIQ_EMBEDDING_BASE_URL` (or remove the
   four model variables to run without a language model: documents then stop yellow for the
   contact and the type, and the search works with words only). The other values fit the
   devcontainer. All variables: [backend/README.md](../backend/README.md#configuration).
3. **Papiq starten** (below).

Keep `PAPIQ_SECRET_KEY` (TOTP and webhook secrets are encrypted with it) and the admin password
in your password manager, not only in `live.env`.

## Start, stop, update

| Task | What it does |
| --- | --- |
| **Papiq starten** | `Papiq: Schema migrieren`, `Papiq: UI bauen`, then `Papiq: API` and `Papiq: Worker` side by side, each in its own terminal |
| **Papiq stoppen** | Ends the API and worker tasks (SIGTERM; the worker finishes running jobs within `PAPIQ_WORKER_SHUTDOWN_TIMEOUT`, 30 s) |
| Papiq: API, Papiq: Worker | One service alone (both run migration first; the API also builds the UI) |
| Papiq: Schema migrieren | `python -m papiq.composition migrate` with `live.env` |
| Papiq: UI bauen | `pnpm build` in `web/`; the API serves `web/build` below `/ui` (`PAPIQ_UI_DIR`) |
| Papiq: UI dev | Vite on <http://localhost:5173/ui/> with `/api` proxied to the API, for UI work with hot reload |
| Papiq: Suche neu aufbauen | `reindex`: rebuild the search index from database and bucket (after a restore, a model change, or to add vectors) |
| Migration: plan, run, verify | The Paperless migration client with the `PAPIQ_MIGRATION_*` variables of `live.env` (see below) |

Nothing starts when the container opens. After **Papiq starten**, open
<http://localhost:8000/ui/> and sign in as the admin from `live.env`; the API documentation is
at <http://localhost:8000/api/v1/docs>. The tasks use the terminals of VS Code; the logs are
there (`PAPIQ_LOG_FORMAT=console`).

**Update after `git pull`:** `uv sync` in `backend/` and `pnpm install` in `web/` when the lock
files changed (the devcontainer runs both after a rebuild), then **Papiq stoppen** and **Papiq
starten**: the start migrates the schema (a new Alembic revision is applied before API and
worker come up) and builds the UI anew. A schema migration cannot be undone: take a backup first
when the pull brings a new revision (`git log --stat -- backend/src/papiq/adapters/outbound/sql/migrations/versions`).

**Rebuild of the devcontainer** ("Rebuild Container"): the volumes stay, `live.env` stays (it is
a file in the workspace), the tasks work as before.

## The language model

`live.env.example` points at Ollama with `qwen3:8b-ctx8k` (classification) and
`snowflake-arctic-embed2` (search vectors), the models evaluated in `.idea/architektur.md`. On a
CPU a document takes 3 to 10 minutes in the model (`PAPIQ_LLM_TIMEOUT=600`); embeddings take
about 0.6 s per section. Single uploads are fine at any time. Mass runs (the migration with
embeddings on, `reindex` after switching embeddings on, a reprocessing of many documents) load
the model's machine for a long time: run them in sections of at most 40 minutes, not after
23:00 CET, and let Ollama unload the model afterwards (`keep_alive=0`). The migration below is
planned without embeddings and gets its vectors from one `reindex` run.

Document content is sent to these endpoints; the start of API and worker warns about every
endpoint outside the local network.

## Memory

Docling needs about 1.1 GB of RAM for a one-page document and up to 2 GB for ten pages, per
job; `live.env.example` sets `PAPIQ_WORKER_CONCURRENCY=3` for a machine with 8 GB (Docker
Desktop or OrbStack: the memory of the VM, not of the Mac). A Docling process killed for lack
of memory ends with exit code -9 in the processing log; the step is repeated, and the document
goes red after three attempts. The table in
[backend/README.md](../backend/README.md#worker) gives the value per RAM.

## Migration from Paperless-ngx

[migration/README.md](../migration/README.md) describes the client. For the development
instance:

1. In Papiq (as the admin): Settings → API tokens → create a token with scope `read_write`;
   write it to the file `PAPIQ_MIGRATION_PAPIQ_TOKEN_FILE` names in `live.env`
   (`/tmp/papiq-token`, outside the repository). Write the Paperless API key to
   `PAPIQ_MIGRATION_PAPERLESS_TOKEN_FILE` (`/tmp/paperless-token`). `/tmp` is emptied when the
   container restarts; recreate the files then.
2. **Migration: plan** reads Paperless and writes nothing; the report in
   `migration/migration-reports/` lists what would happen.
3. **Migration: run** takes everything over (about 12 s per document with OCR and Docling on
   the CPU, no language model: 1500 documents take about 4.5 hours). Ctrl-C in the terminal
   stops cleanly; running the task again continues. The state is in
   `migration/migration-state.sqlite`.
4. **Migration: verify** compares Paperless and Papiq object by object.

Run the migration with the embedding variables commented out in `live.env` (restart API and
worker after changing it): the pipeline then does not call Ollama for every document. Afterwards
switch them on again, restart, and run **Papiq: Suche neu aufbauen** once (about 0.7 s per
document) to get the vectors, in one go while under 40 minutes.

## Backup and restore

What to back up: the database `papiq_live` and the bucket `papiq-live`. Not Meilisearch (the
index is derived; `reindex` rebuilds it). Database first, then the objects (originals never
change; an object that arrived after the database copy is only an unused file). Keep
`PAPIQ_SECRET_KEY` in your password manager.

The devcontainer can talk to Docker on the host (`docker-outside-of-docker`), so the backup uses
the Postgres container and an `rclone` image; `BACKUP` is a folder on the host or in the
workspace (outside the repository, or add it to `.gitignore`).

```sh
BACKUP=/workspaces/papiq/backup/$(date +%Y-%m-%d)
mkdir -p "$BACKUP"
docker exec papiq-dev-postgres-1 pg_dump -U papiq -Fc papiq_live > "$BACKUP/papiq_live.dump"
docker run --rm --network papiq-dev_default -v "$BACKUP:/backup" \
  -e RCLONE_CONFIG_GARAGE_TYPE=s3 -e RCLONE_CONFIG_GARAGE_PROVIDER=Other \
  -e RCLONE_CONFIG_GARAGE_ENDPOINT=http://garage:3900 -e RCLONE_CONFIG_GARAGE_REGION=garage \
  -e RCLONE_CONFIG_GARAGE_ACCESS_KEY_ID=GKdeadbeefdeadbeefdeadbeef \
  -e RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  rclone/rclone:1.75.1 sync garage:papiq-live /backup/objects
```

Restore (into the same devcontainer; stop Papiq first with **Papiq stoppen**):

```sh
docker exec papiq-dev-postgres-1 dropdb -U papiq --if-exists papiq_live
docker exec papiq-dev-postgres-1 createdb -U papiq papiq_live
docker exec -i papiq-dev-postgres-1 pg_restore -U papiq -d papiq_live --no-owner < "$BACKUP/papiq_live.dump"
docker run --rm --network papiq-dev_default -v "$BACKUP:/backup:ro" \
  -e RCLONE_CONFIG_GARAGE_TYPE=s3 -e RCLONE_CONFIG_GARAGE_PROVIDER=Other \
  -e RCLONE_CONFIG_GARAGE_ENDPOINT=http://garage:3900 -e RCLONE_CONFIG_GARAGE_REGION=garage \
  -e RCLONE_CONFIG_GARAGE_ACCESS_KEY_ID=GKdeadbeefdeadbeefdeadbeef \
  -e RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef \
  rclone/rclone:1.75.1 sync /backup/objects garage:papiq-live
```

Then **Papiq starten** and **Papiq: Suche neu aufbauen**. `rclone sync` makes the target equal
to the source: a restore removes objects that are not in the backup. The Garage key above is the
fixed development key from `dev.env`.

## Without VS Code

Every task is a shell command (`.vscode/tasks.json`): load `live.env` and run it, for example

```sh
set -a; . .devcontainer/live.env; set +a
cd backend && uv run python -m papiq.composition api      # and `worker` in a second terminal
```
