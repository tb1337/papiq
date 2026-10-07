# papiq backend

Python package `papiq`, structured as ports and adapters.

```
src/papiq/
  core/            domain core, framework-free
    domain/        entities and value objects (document, drawer, contact, ...)
    ports/         interfaces the core needs (Python protocols)
    services/      use cases: ingest pipeline, rules, permissions
  adapters/
    inbound/       call the core: REST API, MCP server, worker, CLI
    outbound/      implement ports: database, object store, search, LLM, OCR, parser
  composition/     composition root: reads configuration, wires adapters to ports
```

Dependency rules, enforced by `uv run lint-imports`:

- `core` imports nothing from `adapters` or `composition`.
- `core.domain` imports nothing from `core.ports` or `core.services`.
- Inbound and outbound adapters never import each other.
- Only `composition` knows which adapter implements which port.

Every port gets an in-memory adapter for core tests and a contract test suite that every real
adapter must pass.

## Core

- `core/domain`: users, drawers and shares, master data (contacts, document types, tags),
  attribute definitions and values, the document aggregate with the pipeline state machine and
  lanes, domain events, jobs and the permission rules. IDs are UUIDv7, timestamps are UTC.
- `core/ports`: repositories, processing log, outbox and job queue share one `UnitOfWork`, so
  a state change, its events and follow-up jobs are committed together (transactional outbox).
  `EventBus` delivers committed events at least once. Further ports: `ObjectStore`, `Clock`,
  `Ocr`, `DocumentParser`, `PreviewRenderer`; LLM, embeddings, search and identity are designed
  in their milestones.
- `core/services`: use cases (users, drawers, master data, documents, pipeline, maintenance).
  Each runs in one unit of work and checks the caller's rights. Classification, attributes,
  rules and filing are placeholders until M5 and M7.

`papiq.composition.container.build_memory_container()` wires all designed ports to their
in-memory adapters (fakes for OCR, parser and previews); `build_services()` creates the use
cases on top.

## Persistence

`adapters/outbound/sql` implements the unit of work (all repositories, processing log, outbox,
job queue) and the event bus on SQLite or Postgres, selected by `PAPIQ_DB_TYPE`. It uses
SQLAlchemy 2 Core (async; aiosqlite, asyncpg); the domain classes are not ORM-mapped,
`tables.py` defines the tables and the repositories convert explicitly.

- Isolation is read committed with optimistic locking (`version` column) on both databases.
  On SQLite, reads run in autocommit mode and a unit takes the write lock with
  `BEGIN IMMEDIATE` before its first write; writers are serialized. A task must not open a
  second writing unit while its first one is still open: it would wait for its own lock until
  the busy timeout, so the adapter raises RuntimeError at once instead.
- Uniqueness is enforced by the database. Case-insensitive names are stored with a
  `name_key` column (Unicode case folding) under a unique index.
- Timestamps are UTC (`timestamptz`; on SQLite fixed-width UTC text), decimals exact (`numeric`;
  on SQLite text).
- Outbox events are written at commit. On Postgres, an advisory lock makes transactions with
  events commit one at a time, so `outbox.seq` follows commit order and a subscriber's position
  never skips a late commit. The event bus is polled (`dispatch`); handlers run outside of
  transactions; failed deliveries are kept per subscriber in `event_retries` and repeated with
  growing delay (`DeliveryRetry`) until they are given up. `purge` removes events every
  subscription has handled; `JobQueue.purge` removes finished jobs; `JobQueue.release` gives a
  job back unfinished without counting the attempt (`Job.tries`).
- Claiming jobs uses `FOR UPDATE SKIP LOCKED` on Postgres and the write lock on SQLite.

Migrations are one Alembic chain for both databases (batch mode on SQLite), in
`adapters/outbound/sql/migrations`. Apply them with `python -m papiq.composition migrate`; it
creates a missing SQLite file. A schema change needs both an edit of `tables.py` and a new
revision; a test compares the migrated schema with the table definitions.

## Object store

`PAPIQ_STORAGE_TYPE` selects `adapters/outbound/filesystem` or `adapters/outbound/s3`; both pass
the same contract suite. Keys are relative paths (`originals/<sha256>`, `documents/<id>/...`)
checked by `core.ports.object_store.check_key`. `put`/`get` hold an object in memory and suit
small objects; files of any size go through `upload`/`download`, which work on local files.

- Filesystem: one file per key below `PAPIQ_STORAGE_PATH`. Writes go to a hidden temporary file
  next to the target, are flushed to disk and renamed over it, so readers never see a partial
  object.
- S3 (aioboto3): files above 8 MiB are transferred in parts. Checksums are only sent where S3
  requires them, for compatibility with servers such as Garage. Path-style addressing is the
  default (`PAPIQ_S3_PATH_STYLE`).

`Container.aclose()` closes the database engine and the S3 client.

## Processing

Receive → OCR → parse run as jobs (`pipeline.step`); the steps after parsing are placeholders.

| Step | Input | Derivatives (object store) | Outcome |
| --- | --- | --- | --- |
| OCR (`OcrStep`) | `originals/<sha256>` | `documents/<id>/archive.pdf` (PDF/A with text layer), `documents/<id>/preview.webp` (first page, 400 px wide) | uncertain if the archive is not PDF/A |
| Parse (`ParseStep`) | the archive PDF | `documents/<id>/content.md`, `documents/<id>/content.json` (Docling) | failed if no text was recognised |

- A step that raises is retried (`PAPIQ_STEP_MAX_ATTEMPTS`, delay `PAPIQ_STEP_RETRY_DELAY`,
  doubling); after the last attempt the document goes red. A damaged or encrypted file
  (`UnprocessableDocumentError`) fails at once. The processing log records input, output,
  engine version, reason and duration of every run.
- OCRmyPDF (`adapters/outbound/ocrmypdf`) and Docling (`adapters/outbound/docling`) run as
  child processes per document with a time limit (`PAPIQ_OCR_TIMEOUT`, `PAPIQ_PARSE_TIMEOUT`);
  on timeout or cancellation the process group is killed. The job lease is the longer limit
  plus two minutes, so a step normally keeps its claim; if it overruns (e.g. a slow S3
  transfer), another worker takes the job over and the late result is discarded. A step
  interrupted by a stopping worker is released at once (`JobQueue.release`); that does not
  count against `PAPIQ_STEP_MAX_ATTEMPTS`.
- OCRmyPDF: `--skip-text` (pages with text are not recognised again), `--output-type pdfa`,
  languages `PAPIQ_OCR_LANGUAGES`. Images whose stated resolution gives an implausible page
  size (none, or 72 dpi from a phone) are scaled to the long edge of A4; transparency is put on
  white, CMYK is converted, all pages of a TIFF are kept. OCRmyPDF and Docling share the CPU
  cores among `PAPIQ_WORKER_CONCURRENCY` jobs.
- Docling uses the text layer of the archive (no OCR of its own) and the layout and table
  models in `PAPIQ_DOCLING_MODELS_PATH`; it never downloads models (`HF_HUB_OFFLINE=1`). The
  image stage `docling-models` downloads the models of the locked Docling version to
  `/opt/docling-models`; the devcontainer has them. Elsewhere:
  `uv run docling-tools models download layout tableformer -o <dir>`.
- Previews: PDFium (pypdfium2) renders, Pillow encodes WebP. PDFium is not thread-safe; all
  its use in a process shares one lock.
- Known gap: an original is stored before its document is created; if creating fails (e.g.
  the drawer's rights changed meanwhile), the object stays without a document. Removing such
  objects is left to a later cleanup of the object store.

## REST API

`python -m papiq.composition api` (with `PAPIQ_ROLE` `all` or `api`) serves
`adapters/inbound/rest` with Uvicorn on `PAPIQ_API_HOST`:`PAPIQ_API_PORT`. All routes are below
`/api/v1`; the OpenAPI document is `/api/v1/openapi.json`, the interactive docs `/api/v1/docs`.

| Endpoint | Purpose |
| --- | --- |
| `POST /documents` | Upload (multipart: `file`, optional `drawer_id`); `202` with `id`, `status_url` |
| `GET /documents/{id}` | Status: lane, processing state, current step, run, outcomes |
| `GET /documents/{id}/log` | Processing log |
| `POST /documents/{id}/retry` | Repeat the failed step (owner) |
| `POST /documents/{id}/reprocess` | `{"from_step": "ocr"}`: process again from a step (owner) |
| `GET /events` | Server-sent events of the documents the caller may read; `?document_id=` |
| `GET /health` | Database reachable, bucket or storage directory usable; `200` or `503`, no authentication |

- Uploads are streamed into a temporary file and hashed on the way; the limit
  `PAPIQ_UPLOAD_MAX_SIZE` applies while receiving (`413`). The type is recognised from the
  content (PDF, JPEG, PNG, TIFF; otherwise `415`). A file the owner already has: `409` with
  `existing_document_id`.
- Errors are problem details (RFC 9457, `application/problem+json`) and documented per endpoint.
- Authentication comes with M4. Until then the dependency `current_user` answers every request
  that needs a user with `401`; nothing in the running service can name a user. Tests replace
  the dependency.
- Event streams: the API subscribes to the event bus as `api.sse` and polls the outbox every
  `PAPIQ_EVENTS_POLL_INTERVAL`. Each event goes to the streams of the users who may read the
  document at that moment (other users once it is green). No replay: after reconnecting,
  clients fetch the state. `document.deleted` is not streamed (decided in M8). One API instance
  is assumed: several would share the `api.sse` subscription. While the API is down, its
  subscription holds back the purge of the outbox.
- On SIGTERM the event streams end at once, open requests get ten seconds, then the database
  engine and the S3 client are closed.

## Worker

`python -m papiq.composition worker` (with `PAPIQ_ROLE` `all` or `worker`) runs
`adapters/inbound/worker`:

- `PAPIQ_WORKER_CONCURRENCY` loops claim and run due jobs; each waits
  `PAPIQ_WORKER_POLL_INTERVAL` when nothing is due. One loop delivers outbox events
  (`EventBus.dispatch`) every `PAPIQ_EVENTS_POLL_INTERVAL`. Postgres `LISTEN/NOTIFY` is not
  used.
- The cleanup (`maintenance.cleanup`, one job with a fixed dedup key; it goes before pipeline
  steps when due) runs every
  `PAPIQ_CLEANUP_INTERVAL` and removes finished jobs and delivered events older than
  `PAPIQ_RETENTION`.
- SIGTERM or SIGINT: no new jobs; running jobs may finish within
  `PAPIQ_WORKER_SHUTDOWN_TIMEOUT`, then they are cancelled and their jobs released to run again
  at once. Finally the database engine and the S3 client are closed.
- Failed event deliveries are repeated with doubling delay from one second up to an hour, at
  most `PAPIQ_EVENTS_MAX_ATTEMPTS` times. Several dispatchers under the same subscriber name
  may deliver an event twice (at least once is allowed).
- Every loop runs its units of work one after another, never nested, as SQLite requires.

## Configuration

Environment variables with the prefix `PAPIQ_` only; there is no configuration file. Invalid or
contradictory configuration stops the start with a message naming the variables. Check it with
`python -m papiq.composition`, which prints the effective configuration without secrets.

Secrets (marked *secret*) can also be passed as `PAPIQ_<NAME>_FILE=/run/secrets/...` (Docker
secrets): the file content, without one trailing newline, is the value. Setting both
`PAPIQ_<NAME>` and `PAPIQ_<NAME>_FILE` is an error.

Variables for the variant that is not selected (for example `PAPIQ_DB_HOST` with
`PAPIQ_DB_TYPE=sqlite`) are not required, but must still be well-formed if set.
Choices are case-insensitive; surrounding whitespace is removed and blank values count as unset.
Durations are seconds (`30`, `1.5`) or ISO 8601 (`PT1H`, `P7D`) and must be positive.

| Variable | Values / default | Notes |
| --- | --- | --- |
| `PAPIQ_ROLE` | `all` (default), `api`, `worker` | Which services run |
| `PAPIQ_LOG_FORMAT` | `json` (default), `console` | |
| `PAPIQ_LOG_LEVEL` | `INFO` (default), `DEBUG`, `WARNING`, `ERROR`, `CRITICAL` | |
| `PAPIQ_DB_TYPE` | `sqlite` (default), `postgres` | |
| `PAPIQ_DB_SQLITE_PATH` | `data/papiq.db` | SQLite only; local volume (WAL) |
| `PAPIQ_DB_HOST`, `_NAME`, `_USER` | required for `postgres` | |
| `PAPIQ_DB_PORT` | `5432` | |
| `PAPIQ_DB_PASSWORD` | required for `postgres` | *secret* |
| `PAPIQ_STORAGE_TYPE` | `filesystem` (default), `s3` | |
| `PAPIQ_STORAGE_PATH` | `data/objects` | filesystem only |
| `PAPIQ_S3_ENDPOINT_URL`, `_BUCKET` | required for `s3` | |
| `PAPIQ_S3_REGION` | `us-east-1` | |
| `PAPIQ_S3_PATH_STYLE` | `true` | |
| `PAPIQ_S3_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY` | required for `s3` | *secret* |
| `PAPIQ_API_HOST`, `PAPIQ_API_PORT` | `0.0.0.0`, `8000` | Where the API listens |
| `PAPIQ_UPLOAD_MAX_SIZE` | `100MiB` | Largest upload; bytes or with unit (`50MB`, `1GiB`) |
| `PAPIQ_WORKER_CONCURRENCY` | `2` | Jobs at the same time |
| `PAPIQ_WORKER_POLL_INTERVAL` | `1` | Seconds between looks for due jobs |
| `PAPIQ_WORKER_SHUTDOWN_TIMEOUT` | `30` | Seconds running jobs get to finish on SIGTERM |
| `PAPIQ_STEP_MAX_ATTEMPTS` | `3` | Attempts of a pipeline step that raises |
| `PAPIQ_STEP_RETRY_DELAY` | `30` | Seconds before the second attempt, doubling after |
| `PAPIQ_EVENTS_POLL_INTERVAL` | `1` | Seconds between outbox dispatches |
| `PAPIQ_EVENTS_MAX_ATTEMPTS` | `10` | Delivery attempts of an event per subscriber |
| `PAPIQ_CLEANUP_INTERVAL` | `3600` | Seconds between cleanups |
| `PAPIQ_RETENTION` | `P7D` | Age of finished jobs and delivered events to remove |
| `PAPIQ_OCR_LANGUAGES` | `deu+eng` | Tesseract languages, joined by `+` |
| `PAPIQ_OCR_TIMEOUT`, `PAPIQ_PARSE_TIMEOUT` | `600` | Seconds per document and step |
| `PAPIQ_DOCLING_MODELS_PATH` | `/opt/docling-models` | Docling layout and table models |
| `PAPIQ_MEILISEARCH_URL` | unset | required from M6 on |
| `PAPIQ_MEILISEARCH_API_KEY` | unset | *secret* |
| `PAPIQ_LLM_BASE_URL`, `_MODEL` | unset | OpenAI-compatible endpoint; set both or neither |
| `PAPIQ_LLM_API_KEY` | unset | *secret* |
| `PAPIQ_EMBEDDING_BASE_URL`, `_MODEL` | unset | OpenAI-compatible endpoint; set both or neither |
| `PAPIQ_EMBEDDING_API_KEY` | unset | *secret* |

## Tests

- `tests/unit` (marker `unit`): no external services.
- `tests/integration` (marker `integration`): need Postgres, Garage or Meilisearch, the OCR
  programs (Tesseract, Ghostscript) or the Docling models. Which services apply follows the
  `PAPIQ_` variables; a test skips itself if what it needs is not configured or not reachable.
  The devcontainer provides all of them.
- Marker `docling`: the slow tests that run Docling (models load for every document); leave them
  out with `-m "not docling"`.
- Sample files (scan, text PDF, photo, empty page, damaged PDF, Office file) are in
  `tests/samples`, generated by `make_samples.py`.

The markers are applied by directory, so new tests only need to be placed in the right folder.

`tests/contracts` holds the contract suites (classes such as `UnitOfWorkContract`). An adapter's
test module subclasses each suite as `Test...` and provides the adapter fixture
(`uow_factory`, `event_bus_factory`, `object_store`, `clock`, `ocr`, `parser`,
`preview_renderer`); see
`tests/unit/adapters/memory/test_contracts.py`.

The SQL adapter runs the contract suites and `tests/sql_suite.py` (types, concurrency,
migrations) on SQLite in `tests/unit/adapters/sql` (a migrated database file per test) and on
Postgres in `tests/integration/adapters/sql` (a database of its own per test session, emptied
before every test). The CI fails if the Postgres run skips them.
