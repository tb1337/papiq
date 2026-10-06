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
  `EventBus` delivers committed events at least once. Further ports: `ObjectStore`, `Clock`;
  OCR, parser, LLM, embeddings, search and identity are designed in their milestones.
- `core/services`: use cases (users, drawers, master data, documents, pipeline). Each runs in
  one unit of work and checks the caller's rights. Pipeline steps after receive are
  placeholders until M3, M5 and M7.

`papiq.composition.container.build_memory_container()` wires all designed ports to their
in-memory adapters; `build_services()` creates the use cases on top.

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

| Variable | Values / default | Notes |
| --- | --- | --- |
| `PAPIQ_ROLE` | `all` (default), `api`, `worker` | Which services run |
| `PAPIQ_LOG_FORMAT` | `json` (default), `console` | |
| `PAPIQ_LOG_LEVEL` | `INFO` (default), `DEBUG`, `WARNING`, `ERROR`, `CRITICAL` | |
| `PAPIQ_DB_TYPE` | `sqlite` (default), `postgres` | |
| `PAPIQ_DB_SQLITE_PATH` | `data/papiq.db` | SQLite only |
| `PAPIQ_DB_HOST`, `_NAME`, `_USER` | required for `postgres` | |
| `PAPIQ_DB_PORT` | `5432` | |
| `PAPIQ_DB_PASSWORD` | required for `postgres` | *secret* |
| `PAPIQ_STORAGE_TYPE` | `filesystem` (default), `s3` | |
| `PAPIQ_STORAGE_PATH` | `data/objects` | filesystem only |
| `PAPIQ_S3_ENDPOINT_URL`, `_BUCKET` | required for `s3` | |
| `PAPIQ_S3_REGION` | `us-east-1` | |
| `PAPIQ_S3_PATH_STYLE` | `true` | |
| `PAPIQ_S3_ACCESS_KEY_ID`, `_SECRET_ACCESS_KEY` | required for `s3` | *secret* |
| `PAPIQ_MEILISEARCH_URL` | unset | required from M6 on |
| `PAPIQ_MEILISEARCH_API_KEY` | unset | *secret* |
| `PAPIQ_LLM_BASE_URL`, `_MODEL` | unset | OpenAI-compatible endpoint; set both or neither |
| `PAPIQ_LLM_API_KEY` | unset | *secret* |
| `PAPIQ_EMBEDDING_BASE_URL`, `_MODEL` | unset | OpenAI-compatible endpoint; set both or neither |
| `PAPIQ_EMBEDDING_API_KEY` | unset | *secret* |

## Tests

- `tests/unit` (marker `unit`): no external services.
- `tests/integration` (marker `integration`): need Postgres, Garage or Meilisearch. Which services
  apply follows the `PAPIQ_` variables; a test skips itself if its service is not configured or not
  reachable. The devcontainer provides all three.

The markers are applied by directory, so new tests only need to be placed in the right folder.

`tests/contracts` holds the contract suites (classes such as `UnitOfWorkContract`). An adapter's
test module subclasses each suite as `Test...` and provides the adapter fixture
(`uow_factory`, `event_bus`, `object_store`, `clock`); see
`tests/unit/adapters/memory/test_contracts.py`.
