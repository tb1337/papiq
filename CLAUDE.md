# CLAUDE.md

Papiq (p:api:q) is a self-hosted, headless document management system, a replacement for
Paperless-ngx. Python backend, hexagonal (ports and adapters); the REST API is the only way in.

## Layout

| Path | Content |
| --- | --- |
| `backend/` | Python package `papiq`: `core` (domain, ports, services), `adapters` (inbound, outbound), `composition` |
| `web/` | Web UI (SvelteKit, not started) |
| `migration/` | Paperless-ngx migration client (not started) |
| `deploy/` | Production image and Compose stacks (M9) |
| `.devcontainer/` | Devcontainer: Compose services (Postgres, Garage, Meilisearch) and dev credentials |
| `Dockerfile` | Stages `dev` (devcontainer) and `runtime` (placeholder) |
| `.idea/` | Design documents in German: `architektur.md` (binding), `umsetzungsplan.md` |

## Commands

Run in `backend/` (inside the devcontainer, where all services are available):

```sh
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest                    # all tests; integration tests skip if a service is unreachable
uv run pytest -m unit            # no external services
uv run pytest -m integration     # needs Postgres, Garage or Meilisearch
uv run python -m papiq.composition   # validate PAPIQ_ configuration
uv run python -m papiq.composition migrate   # bring the configured database to the newest schema
```

Run all five checks before every commit.

## Architecture rules

- `core` is plain Python: no framework imports (no FastAPI, SQLAlchemy, Pydantic settings, structlog,
  S3 or HTTP clients). It logs through the standard library `logging`.
- Ports are Python protocols defined in `core/ports`; adapters implement them.
- Only `composition` knows which adapter implements which port. Inbound and outbound adapters never
  import each other. `lint-imports` enforces this; keep it green.
- Every port gets an in-memory adapter and a contract test suite that every real adapter passes.
- Configuration is environment variables with prefix `PAPIQ_` only, no config file. Secrets also work
  as `PAPIQ_<NAME>_FILE`; both set is an error. See `backend/src/papiq/composition/settings.py`.
- Async throughout (FastAPI, SQLAlchemy async, async HTTP clients).
- Tests: `tests/unit` (marker `unit`) needs no services, `tests/integration` (marker `integration`) does.
  Markers are applied by directory. Contract suites live in `tests/contracts`; adapters subclass them.
  The SQL adapter runs them on SQLite (unit) and Postgres (integration).
- Schema changes: change `adapters/outbound/sql/tables.py` and add an Alembic revision in
  `adapters/outbound/sql/migrations/versions`; a test fails if the two differ.
- State change, domain events and follow-up jobs go through one `UnitOfWork` and one commit.

## Language

Code, comments, commit messages and documentation (README, docstrings) are English. The design
documents in `.idea/` are German.

## Working style

- Plan first: read the design documents, propose a plan, wait for approval.
- If something is unclear or a decision is missing, ask before implementing. Do not deviate from
  the decisions in `.idea/architektur.md` on your own.
- New dependencies need agreement first.
- Small, thematically separate commits. Keep answers short and precise.
