# papiq deploy

Papiq ships as a single Docker image. s6-overlay runs as PID 1 and supervises the Papiq
processes inside it:

| Service | Type | Purpose |
| --- | --- | --- |
| `init-papiq` | oneshot | validate configuration, prepare volumes (`PUID`/`PGID`) |
| `init-migrations` | oneshot | run Alembic migrations before API and worker start |
| `svc-api` | longrun | Uvicorn with FastAPI: REST, MCP, SSE, web UI files |
| `svc-worker` | longrun | pipeline jobs, outbox dispatch, webhook delivery |

Outside the Papiq container: Postgres (if used), Garage (if S3 is used), Meilisearch, LLM,
identity provider. With SQLite and the filesystem adapter, database and files live on volumes
of the Papiq container.

Configuration: entirely through environment variables prefixed `PAPIQ_`; no config file.
Secrets can be passed as Docker secrets via `PAPIQ_…_FILE` (e.g.
`PAPIQ_DB_PASSWORD_FILE=/run/secrets/db`). `PAPIQ_ROLE=all|api|worker` selects which services
s6 starts (default `all`).

Image: Debian slim base, OCRmyPDF and Docling included, PyTorch CPU build.

Not started.
