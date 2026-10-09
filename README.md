# p:api:q

**Papiq** is a self-hosted, headless document management system and a replacement for
Paperless-ngx. Every feature is exposed through the REST API; the web UI, the MCP server for AI
clients and the Paperless migration tool are clients of that API. Documents run through OCR,
parsing and a classification whose confidence comes from verifiable facts, and end up in one of
three lanes: green (done), yellow (confirm), red (intervene).

> Status: **0.1** – the first release. One person's archive of about 1500 documents runs on it,
> taken over from Paperless-ngx with the migration client. Expect rough edges; the API is the
> contract, the UI covers it.

## What it does

- **Multi-user.** Native logins with optional TOTP, optional OpenID Connect. Permissions are
  attached to drawers (filing units that can be shared), never to single documents; admins see
  and manage everything.
- **Explainable ingest.** Receive → OCR (OCRmyPDF, PDF/A) → parse (Docling) → classify
  (language model, OpenAI-compatible: Ollama or cloud) → extract attributes → apply rules → file.
  Every step is logged with input, result, confidence, model version and duration, and can be
  repeated. A value the model proposes counts only if it occurs in the text; an unknown contact
  or type makes the document yellow, never a silent guess.
- **Rules.** Global rules (admins) and personal rules, built in the UI from conditions and
  actions, versioned, with a dry run before changes and retroactive application on request.
- **Search.** Hybrid search in Meilisearch: words and meaning (vectors) in one query, filtered by
  the caller's rights.
- **Integration.** Webhooks (signed, Standard Webhooks), server-sent events, an MCP server at
  `/api/v1/mcp` for Claude and other AI clients, OpenAPI with a generated TypeScript client.
- **Storage.** SQLite or Postgres, local file system or S3 (Garage), Meilisearch. Originals are
  immutable and addressed by their SHA-256.

## Quick start

**Run it with Docker** (the production shape: one image with API and worker under s6-overlay,
plus Meilisearch, optionally Postgres and Garage): [deploy/README.md](deploy/README.md).

```sh
docker build --target runtime -t papiq:local .
cd deploy && ./create-secrets.sh && docker compose -f compose.sqlite.yml up -d --build
```

Then open <http://localhost:8000/ui/> and sign in as `admin` with the password in
`deploy/secrets/admin_password`. Point `PAPIQ_LLM_BASE_URL` and `PAPIQ_EMBEDDING_BASE_URL` at an
Ollama or another OpenAI-compatible endpoint; without one, documents stop yellow for a person to
classify and the search works with words only.

**Run it from the devcontainer** (development, or a personal instance on your machine): open the
repository in VS Code, "Reopen in Container", run the task **Papiq: Devinstanz einrichten**, fill
in `.devcontainer/live.env`, run **Papiq starten**. Details, data separation, backup and the
migration from Paperless: [.devcontainer/README.md](.devcontainer/README.md).

**Take a Paperless-ngx archive over:** [migration/README.md](migration/README.md) (`plan`, `run`,
`verify`; only through the two APIs).

## Documentation

| Document | Content |
| --- | --- |
| [backend/README.md](backend/README.md) | Architecture of the backend, every `PAPIQ_` variable with default, role and meaning, the REST API, processing, classification, rules, search, webhooks, MCP, tests |
| [deploy/README.md](deploy/README.md) | The image: roles, user and volumes, health and logs, stopping, reverse proxy, backup and restore, updating |
| [.devcontainer/README.md](.devcontainer/README.md) | Development and the development instance in the devcontainer: tasks, data separation, Ollama, memory, migration, backup |
| [web/README.md](web/README.md) | The web UI: stack, commands, API client, CSP |
| [migration/README.md](migration/README.md) | The Paperless-ngx migration client |
| `.idea/` | Design documents in German: `architektur.md` (binding), `umsetzungsplan.md`, `prompts/`, `reviews/` |

## Principles

- **Headless.** The REST API (OpenAPI) is the only way in. No back doors for the UI or the
  migration.
- **Hexagonal.** The domain core knows no database, object store, search engine or LLM, only
  ports it defines itself. Every technology lives in a replaceable adapter with a contract test.
- **Asynchronous.** API calls return at once (`202 Accepted`); processing runs in the worker.
  State changes are published as events (transactional outbox) to the UI, webhooks and the
  search index.
- **Configuration by environment.** Variables with the prefix `PAPIQ_`, secrets also as files;
  no configuration file.

## Glossary

| Term | Meaning |
| --- | --- |
| Document | A file with metadata. Has an owner, exactly one drawer, at most one contact and one type. |
| Contact | The other party of a document (Paperless: correspondent). Global. |
| Document type | Kind of document. Global; brings its own attributes. |
| Tag | Free classification, any number per document. Carries no permissions. |
| Attribute | User-defined field (Paperless: custom field). Scope: global or per document type. |
| Drawer | Filing and permission unit. Has an owner, can be shared with other users. |
| Inbox | Where yellow and red documents wait for their owner. |
| Lane | Processing outcome: green, yellow or red. |
| Rule | Trigger, conditions and actions. Global rules by admins, personal rules by each user. |

## Development

Everything runs in the devcontainer (Python 3.13, uv, Node.js, pnpm, OCRmyPDF, Docling models,
Postgres, Garage, Meilisearch). The checks, in `backend/`:

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest -n auto            # unit and integration tests
```

Web UI in `web/`: `pnpm lint && pnpm format:check && pnpm check && pnpm test && pnpm build`.
Migration client in `migration/`: `uv run ruff check . && uv run mypy && uv run pytest`.
`CLAUDE.md` holds the rules for contributions: architecture, commits, labels, language.

## License

[GPL-3.0](LICENSE)
