# papiq deploy

Papiq ships as a single Docker image. s6-overlay runs as PID 1 and supervises the Papiq
processes inside it:

| Service | Type | Purpose |
| --- | --- | --- |
| `init-papiq` | oneshot | check `PUID`/`PGID`, prepare the volume, validate the configuration |
| `init-migrations` | oneshot | `PAPIQ_ROLE` `all`/`api`: migrate the database; `worker`: nothing |
| `svc-api` | longrun | Uvicorn with FastAPI: REST, MCP, SSE, and the web UI below `/ui` |
| `svc-worker` | longrun | waits for the schema (role `worker`), then pipeline jobs, outbox dispatch, webhook delivery |

Outside the Papiq container: Postgres (if used), Garage (if S3 is used), Meilisearch, the
language model, an identity provider. With SQLite and the file system, the database and the files
live on one volume of the Papiq container.

Image: Debian slim, Python 3.13, OCRmyPDF with Tesseract (German, English), Docling with the CPU
build of PyTorch and its models (never downloaded at run time), s6-overlay 3.2.3.2, the built web
UI in `/opt/papiq/ui` (static files; no Node.js in the image). For `amd64` and `arm64`. About 3 GB (`docker image inspect`). Nothing secret is part of the image.

```sh
docker build --target runtime -t papiq:local .     # in the repository root
deploy/test-image.sh papiq:local                   # a few minutes: starts, migrates, health, user, web UI, roles, stop
```

Publishing the image comes with the release (M13); until then you build it yourself.

## Start an example stack

Two Compose files, both with Meilisearch; the language model is outside (see
[Language model](#language-model)).

| File | Database | Files | Volumes |
| --- | --- | --- | --- |
| `compose.sqlite.yml` | SQLite | file system | `papiq-data` (`/data`), `meilisearch-data` |
| `compose.postgres.yml` | Postgres 17 | Garage (S3) | `postgres-data`, `garage-meta`, `garage-data`, `meilisearch-data` |

```sh
cd deploy
./create-secrets.sh                                    # once: ./secrets (not in the repository)
docker compose -f compose.sqlite.yml up -d --build     # or compose.postgres.yml
docker compose -f compose.sqlite.yml ps                # wait for "healthy"
```

Open <http://localhost:8000/api/v1/docs> (another port: `PAPIQ_HTTP_PORT=8080 docker compose …`).
Sign in as `admin`; the password is in `secrets/admin_password`. The stacks publish the port on
`127.0.0.1` only and run over plain HTTP (`PAPIQ_COOKIE_SECURE=false`): put a TLS proxy in front
before anyone else uses it (see [Reverse proxy](#reverse-proxy)).

## Configuration

Environment variables with the prefix `PAPIQ_`, no configuration file; the Compose files show
the ones that matter. The start stops with a message naming every invalid variable (`docker compose
up` without `-d`, or `docker compose logs papiq`). A running container prints its effective
configuration, secrets masked, with `docker compose exec papiq papiq check`. The complete list is
in [backend/README.md](../backend/README.md#configuration).

**Secrets** are passed as files (Docker secrets): `PAPIQ_<NAME>_FILE=/run/secrets/…`; the file's
content is the value, and setting both `PAPIQ_<NAME>` and `PAPIQ_<NAME>_FILE` is an error. The files
must be readable by `PUID`:`PGID`, because the services read them as that user.
Pass secrets only this way: a secret in a plain `PAPIQ_…` variable is also stored, readable by
every process, in `/run/s6/container_environment` inside the container, and shows in `docker inspect`.
`create-secrets.sh` writes them with random values (`openssl rand`), keeps files that exist, and
gives Garage's two secrets the mode `0600` it insists on. By hand:

```sh
openssl rand -base64 32 > secrets/papiq_secret_key   # PAPIQ_SECRET_KEY: 32 random bytes, base64
```

`PAPIQ_SECRET_KEY` is required by every service (it encrypts TOTP and webhook secrets). Losing it
means losing both. The key from `.devcontainer/dev.env` is public and refused. Meilisearch has no
`*_FILE` variable: its key reaches it through `secrets/meilisearch.env`, the one secret that is an
environment variable of a container (visible in `docker inspect` of Meilisearch, not of Papiq).

| Variable | Default | Notes |
| --- | --- | --- |
| `PAPIQ_ROLE` | `all` | `all`, `api` or `worker` (next section) |
| `PAPIQ_SECRET_KEY(_FILE)` | – | required |
| `PAPIQ_ADMIN_USERNAME`, `PAPIQ_ADMIN_PASSWORD(_FILE)` | – | create the first admin while there is none |
| `PAPIQ_DB_TYPE` | `sqlite` | `postgres` needs `PAPIQ_DB_HOST`, `_NAME`, `_USER`, `_PASSWORD(_FILE)` |
| `PAPIQ_STORAGE_TYPE` | `filesystem` | `s3` needs `PAPIQ_S3_ENDPOINT_URL`, `_BUCKET`, `_ACCESS_KEY_ID(_FILE)`, `_SECRET_ACCESS_KEY(_FILE)` |
| `PAPIQ_MEILISEARCH_URL`, `_API_KEY(_FILE)` | – | search; without it Papiq works without search |
| `PAPIQ_LLM_BASE_URL`, `_MODEL` | – | set both or neither; `PAPIQ_LLM_TIMEOUT` (seconds, 300) |
| `PAPIQ_EMBEDDING_BASE_URL`, `_MODEL`, `_DIMENSIONS`, `_QUERY_PREFIX` | – | with Meilisearch and embeddings the dimensions are required |
| `PAPIQ_COOKIE_SECURE`, `PAPIQ_FORWARDED_ALLOW_IPS`, `PAPIQ_PUBLIC_URL` | `true`, –, – | behind a proxy, see below |
| `PAPIQ_UPLOAD_MAX_SIZE` | 100 MiB | the proxy's body limit must allow it |
| `PAPIQ_UI_DIR` | `/opt/papiq/ui` | the web UI's files; unset it to serve the API only |
| `PAPIQ_WORKER_CONCURRENCY`, `PAPIQ_WORKER_SHUTDOWN_TIMEOUT` | 2, 30 s | see [Stopping](#stopping) |
| `PAPIQ_MCP_ENABLED`, `PAPIQ_MCP_TEXT_MAX` | `true`, 20000 | the MCP endpoint and the characters one `get_text` returns |
| `PAPIQ_WEBHOOKS_PER_USER` | 20 | webhooks per user |
| `PAPIQ_WEBHOOK_TIMEOUT`, `_MAX_ATTEMPTS`, `_RETRY_DELAY` | 10 s, 10, 30 s | per attempt; attempts per event; first delay, doubling up to one hour |
| `PAPIQ_WEBHOOK_DISABLE_AFTER`, `_CONCURRENCY`, `_SECRET_GRACE` | 20, 4, 24 h | given-up deliveries in a row until a webhook is switched off; parallel deliveries; how long a renewed secret's predecessor still signs |
| `PAPIQ_LOG_FORMAT`, `PAPIQ_LOG_LEVEL` | `json`, `INFO` | all services log to stdout/stderr |

Variables of the image itself (not `PAPIQ_`, so not checked as settings): `PUID`, `PGID`, and the
`S6_*` variables of s6-overlay.

### Language model

Papiq talks to any OpenAI-compatible endpoint; the examples point at Ollama on the host
(`host.docker.internal:11434`) and use `qwen3:8b` and `snowflake-arctic-embed2`. Change the address
with `PAPIQ_LLM_BASE_URL`, `PAPIQ_EMBEDDING_BASE_URL` and the models with `PAPIQ_LLM_MODEL`,
`PAPIQ_EMBEDDING_MODEL` in your shell (empty: none). Start Ollama with `OLLAMA_CONTEXT_LENGTH=8192`
or more; it cuts longer prompts silently. On a CPU a document takes minutes (`PAPIQ_LLM_TIMEOUT`
is 600 in the examples). Document content is sent to these endpoints; at start, a warning names
every endpoint outside the local network.

## Roles

`PAPIQ_ROLE` decides which services s6 starts; the other one stays down (`s6-svstat` shows it).

| Role | Starts | Migrates | Health |
| --- | --- | --- | --- |
| `all` (default) | API and worker | yes | API answers and worker is up |
| `api` | API | yes | API answers |
| `worker` | worker | never | worker is up |

Separate containers run the same image: for example two services in one Compose file, one with
`PAPIQ_ROLE: api`, one with `worker`, otherwise the same environment. The worker waits for the API
container to migrate (`svc-worker` runs `check-schema --wait 600`, then looks again; the container
reports unhealthy meanwhile, and `docker stop` ends the wait at once). A database migrated by a
newer image is refused with a message every ten seconds. Run a single API instance: the event
streams assume it.

## User and volumes

s6 starts as root. Every Papiq process (configuration check, migration, API, worker and the OCR
and Docling processes they start) runs as `PUID`:`PGID` (default `1000:1000`), never as root;
`0` is refused. `init-papiq` makes that user the owner of `/data` (and of the paths you set with
`PAPIQ_DB_SQLITE_PATH` and `PAPIQ_STORAGE_PATH`): recursively only if the folder itself belongs to
someone else, otherwise only the database files, so a large object store is not walked at each start.
The caches of the libraries go to a `HOME` that disappears with the container.

- `/data` is the only volume. The image sets `PAPIQ_DB_SQLITE_PATH=/data/papiq.db` and
  `PAPIQ_STORAGE_PATH=/data/objects`. With Postgres and S3 it stays empty.
- SQLite needs a local disk in WAL mode, never a network share. A share that does not allow
  `chown` makes `init-papiq` fail with that message: set the owner on the share instead.
- After restoring files into the volume as root, give them to `PUID`:`PGID` again (the restore
  commands below do).
- Run commands of the application with `papiq` (it is `python -m papiq.composition` as that user):
  `docker compose exec papiq papiq reindex`, `… papiq check-schema`.

## Health and logs

The image has a Docker `HEALTHCHECK` (every 30 s, 120 s to start): the API through
`GET /api/v1/health` (status `ok` or `degraded` is healthy; `unavailable`, a database or object
store that cannot be reached, is not) and the worker through its s6 service (up for at least ten
seconds, so a crash loop shows, and past its wait for the schema). The worker has no endpoint of
its own: a hung worker that is still a process is not detected. Compose `depends_on` can use `condition: service_healthy`.

All services write to stdout/stderr of the container (`docker compose logs papiq`), as JSON by
default. s6's own messages (`s6-rc: info: …`) are there as well. If an init service fails (invalid
configuration, failed migration), the container stops with exit code 1 and the reason is the last
thing in the log.

## Stopping

`docker stop` sends SIGTERM to s6, which stops the services together: Uvicorn ends its streams and
gives requests 10 s; the worker takes no new jobs and gives running ones up to
`PAPIQ_WORKER_SHUTDOWN_TIMEOUT` (30 s) before it hands them back to run again. The values fit
together:

| | Default | Rule |
| --- | --- | --- |
| `PAPIQ_WORKER_SHUTDOWN_TIMEOUT` | 30 s | |
| `S6_SERVICES_GRACETIME` | 40000 ms | at least (worker timeout + 10 s) in ms, then s6 kills the service |
| `stop_grace_period` (Compose) / `docker stop -t` | 60 s | above the s6 value, or Docker kills the whole container |

Raise all three together if you raise the worker's timeout. An idle container stops in about two
seconds with exit code 0, a worker that waits for the schema as well. A migration that is running
cannot be interrupted: `docker stop` waits for it up to `stop_grace_period`, then kills it; the
migration is one transaction and is rolled back.

## Reverse proxy

Put a TLS proxy in front of the API and remove `PAPIQ_COOKIE_SECURE: "false"` (default `true`: the
session cookie is `Secure`). Then name the proxy: `PAPIQ_FORWARDED_ALLOW_IPS` takes its address
(comma-separated, `*` only if the proxy sets `X-Forwarded-For` itself and replaces what clients
send); Papiq counts failed sign-ins per client address from that header, and refuses to start
with secure cookies and without it. With OIDC set `PAPIQ_PUBLIC_URL`.

The web UI is at `/ui/` (`/` leads there). Papiq sets its cache headers: files below
`/ui/_app/immutable/` carry a hash in their name and may be cached for a year, everything else
(`no-cache`) is revalidated on every load, so an update shows at once. A proxy cache keeps these
headers; it must not cache `/api/` (the API's answers are personal).

Two paths must not be buffered or cut short by the proxy:

- `/api/v1/events`: server-sent events, minutes to hours.
- `/api/v1/mcp`: MCP (Streamable HTTP, stateless; plain `POST`s with JSON answers).

For nginx:

```nginx
client_max_body_size 100m;                 # PAPIQ_UPLOAD_MAX_SIZE
location / {
    proxy_pass http://papiq:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
}
location ~ ^/api/v1/(events|mcp) {
    proxy_pass http://papiq:8000;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $remote_addr;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_buffering off;
    proxy_read_timeout 1h;
}
```

## Webhooks

The worker delivers webhooks, so the worker container needs network access to their targets, also
in your own network. Papiq follows no redirects and ignores the proxy environment variables
(`HTTP_PROXY` and the like), so the targets must be reachable directly from the worker's network. A
target that does not answer holds up deliveries up to `PAPIQ_WEBHOOK_TIMEOUT` each, at most
`PAPIQ_WEBHOOK_CONCURRENCY` at a time; processing documents is not affected.

## MCP clients

The API serves MCP at `/api/v1/mcp`. Create a token (`POST /api/v1/auth/tokens`, see [backend/README.md](../backend/README.md#mcp)) and
add it, for example, to Claude Code:

```sh
claude mcp add --transport http papiq http://localhost:8000/api/v1/mcp \
  --header "Authorization: Bearer papiq_..."
```

## Backup and restore

What to back up: the **database** and the **object store** (the original files and their
derivatives). **Not** Meilisearch: the index is derived; after a restore `papiq reindex` rebuilds
it. Keep the secrets (`PAPIQ_SECRET_KEY` above all) in your password manager, not only in the
backup. Back up the database first, then the objects: the originals never change (their key is the SHA-256
of the content), so objects that came in after the database copy are only unused files, while the
other order could leave documents without a file. The derivatives (archive PDF, text, preview) are
written again when a document is processed again; a copy of an older state is still usable, and
reprocessing the document renews it.

### SQLite and file system

Do not copy `papiq.db` while Papiq runs: with WAL, the content is partly in `papiq.db-wal`. Use
SQLite's backup, which Python in the image provides (no `sqlite3` program needed):

```sh
mkdir backup                       # a new, empty folder for each backup
# -u is your PUID:PGID: SQLite needs write access to the volume for its shared-memory file.
docker compose -f compose.sqlite.yml exec -u 1000:1000 papiq python -c "
import sqlite3
source = sqlite3.connect('/data/papiq.db')
target = sqlite3.connect('/tmp/papiq-backup.db')
source.backup(target)
target.close()"
docker compose -f compose.sqlite.yml cp papiq:/tmp/papiq-backup.db backup/papiq.db
docker compose -f compose.sqlite.yml exec papiq rm /tmp/papiq-backup.db
docker compose -f compose.sqlite.yml cp papiq:/data/objects backup/
```

Restore into an empty or existing stack (the database and the objects in the volume are replaced):

```sh
docker compose -f compose.sqlite.yml down                    # keeps the volumes
docker run --rm -v papiq-sqlite_papiq-data:/data -v "$PWD/backup:/backup:ro" alpine:3.24.2 sh -c '
  rm -rf /data/papiq.db /data/papiq.db-wal /data/papiq.db-shm /data/objects &&
  cp /backup/papiq.db /data/papiq.db && cp -a /backup/objects /data/objects &&
  chown -R 1000:1000 /data'                                  # PUID:PGID
docker compose -f compose.sqlite.yml up -d
docker compose -f compose.sqlite.yml exec papiq papiq reindex   # rebuild the search index
```

### Postgres and Garage

```sh
mkdir -p backup
docker compose -f compose.postgres.yml exec -T postgres pg_dump -U papiq -Fc papiq > backup/papiq.dump
# Objects: Garage's two volumes, with Garage stopped (a consistent copy of its metadata).
docker compose -f compose.postgres.yml stop garage
docker run --rm -v papiq-postgres_garage-meta:/meta:ro -v papiq-postgres_garage-data:/data:ro \
  -v "$PWD/backup:/backup" alpine:3.24.2 tar -C / -czf /backup/garage.tar.gz meta data
docker compose -f compose.postgres.yml start garage
```

Restore into a stack with the same Compose secrets (Garage's key, Postgres password):

```sh
docker compose -f compose.postgres.yml down
docker compose -f compose.postgres.yml up -d postgres
docker compose -f compose.postgres.yml exec -T postgres pg_restore -U papiq -d papiq \
  --clean --if-exists --no-owner < backup/papiq.dump
docker run --rm -v papiq-postgres_garage-meta:/meta -v papiq-postgres_garage-data:/data \
  -v "$PWD/backup:/backup:ro" alpine:3.24.2 sh -c 'rm -rf /meta/* /data/* && tar -C / -xzf /backup/garage.tar.gz'
docker compose -f compose.postgres.yml up -d
docker compose -f compose.postgres.yml exec papiq papiq reindex
```

Another S3 server or an external Postgres: use its own tools (`pg_dump`, `rclone sync`); the order
stays the same. Test a restore before you need it.

## Updating

Build or pull the new image and `docker compose up -d`: the API container migrates the database
before it starts. A schema upgrade cannot be undone; back up first. With separate containers,
update the API container first: a worker that starts waits for the schema, and a database that a
newer image migrated is refused by an older one.
