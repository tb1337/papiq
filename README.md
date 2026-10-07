# p:a~p~i:q

**Papiq** is a self-hosted, headless document management system — a replacement for
Paperless-ngx, built as a Docker stack. Every feature is exposed through the API; the web UI,
the MCP server and the Paperless migration tool are clients of that API.

> Status: early design phase. Nothing here is usable yet.

## Principles

- **Headless.** The REST API (OpenAPI) is the only way in. No back doors for the UI.
- **Hexagonal.** The domain core knows no database, object store, search engine or LLM — only
  ports it defines itself. Every technology lives in a replaceable adapter.
- **Asynchronous.** API calls return immediately (`202 Accepted`); processing runs in the
  background. State changes are published as events (transactional outbox) to the web UI,
  webhooks and the search index. Async Python throughout.
- **Explainable ingest.** Every document runs through retryable steps and ends up in one of
  three lanes: green (done), yellow (needs confirmation), red (needs intervention).
- **Multi-user.** Native logins with optional TOTP, optional OIDC. Permissions are attached to
  drawers, never to single documents.

## Repository layout

| Path | Content |
| --- | --- |
| `backend/` | Python package `papiq`: domain core, ports, adapters (API, MCP, worker) |
| `web/` | Web UI (single-page app on top of the API) |
| `migration/` | Migration client from Paperless-ngx |
| `deploy/` | Docker image (s6-overlay) and Compose stack |
| `.idea/` | Design documents in German: architecture, implementation plan |

## Glossary

| Term | Meaning |
| --- | --- |
| Document | A file with metadata. Has an owner, exactly one drawer, one contact, one type. |
| Contact | The other party of a document (Paperless: correspondent). Global. |
| Document type | Kind of document. Global; brings its own attributes. |
| Tag | Free classification, any number per document. Carries no permissions. |
| Attribute | User-defined field (Paperless: custom field). Scope: global or per document type. |
| Drawer | Filing and permission unit. Has an owner, can be shared with other users. |
| Inbox | Where yellow and red documents wait for their owner. |
| Lane | Processing outcome: green, yellow or red. |
| Rule | Trigger, conditions and actions. Global rules by admins, personal rules by each user. |

## Development

Develop inside the devcontainer (VS Code: "Reopen in Container"). It starts the `dev` container
(Python 3.13, uv, Node.js, pnpm, OCRmyPDF system packages) together with Postgres, Garage
(initialised with a bucket and a fixed development key) and Meilisearch, and sets the matching
`PAPIQ_` variables. The credentials in `.devcontainer/dev.env` are for local use only.

The backend uses [uv](https://docs.astral.sh/uv/). In the devcontainer, `uv sync` runs
automatically after creation.

```sh
cd backend
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run lint-imports
uv run pytest
```

No language model runs in the devcontainer. To use an Ollama on the host, set
`PAPIQ_LLM_BASE_URL=http://host.docker.internal:11434/v1` (and `PAPIQ_LLM_MODEL`); see
`.devcontainer/dev.env`.

Configuration is documented in [backend/README.md](backend/README.md#configuration).

## License

[GPL-3.0](LICENSE)
