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
  `EventBus` delivers committed events at least once. The identity repositories (credentials,
  sessions, API tokens, external identities, failed sign-ins) are part of the unit of work too.
  Further ports: `ObjectStore`, `Clock`, `Ocr`, `DocumentParser`, `PreviewRenderer`,
  `PasswordHasher`, `SecretCipher`, `Totp`, `OidcProvider`, `LanguageModel`, `Embeddings`;
  search is designed in its milestone.
- `core/services`: use cases (users, drawers, master data, documents, pipeline, inbox,
  classification, maintenance). Each runs in one unit of work and checks the caller's rights.
  Rules and filing are placeholders until M7.

`papiq.composition.container.build_memory_container()` wires all designed ports to their
in-memory adapters (fakes for OCR, parser and previews); `build_services()` creates the use
cases on top.

## Identity

`core/domain/identity.py` and `core/services/auth.py`; `User` itself only knows its role and
whether it is active. A deactivated user has no rights and cannot authenticate.

- Passwords: Argon2id (`adapters/outbound/crypto`, RFC 9106 low-memory profile: 3 passes,
  64 MiB, 4 lanes), at most four hashes at a time, in a worker thread. Normalised to Unicode
  NFKC; 12 to 256 characters, not the username, no rules on character classes. Hashes with
  older parameters are replaced at the next sign-in.
- Sign-in: unknown and deactivated users are checked against a dummy hash, so all failures take
  the same time and read the same. Failures are counted per account (also for unknown names;
  from the sixth on, the account backs off from 1 second, doubling up to 15 minutes; no hard
  lock) and per source address (30 within 15 minutes block it for 15 minutes; IPv6 addresses
  count per /64 network); the counts are
  in the database. Every attempt is counted atomically before it is checked (an upsert),
  blocking as if it fails, and taken back if it does not fail, so attempts that arrive
  together cannot pass the throttle at once. A blocked account refuses every attempt (`429`), also a correct password, so
  TOTP codes cannot be guessed. A blocked source refuses only wrong sign-ins (`429` instead of
  `401`, not counted for the source again); a correct sign-in from there succeeds, so failures
  from an address many users share (a proxy, NAT) lock nobody out. Success clears the
  account's count.
- TOTP (RFC 6238, pyotp), optional per user: set up with a new secret, which takes effect when a
  code confirms it; then ten recovery codes (80 bits each) are shown once and stored as SHA-256
  hashes. Codes of the previous and next 30-second step are accepted, each step only once. The
  secret is encrypted with AES-256-GCM (`PAPIQ_SECRET_KEY`), bound to the user id. Turning TOTP
  off needs a code or a recovery code, or an admin.
- Sessions: random 256-bit tokens, stored as SHA-256. They end after `PAPIQ_SESSION_IDLE_TIMEOUT`
  without use, after `PAPIQ_SESSION_MAX_AGE`, at sign-out, at deactivation and when the password
  changes or is reset (the caller's own session is renewed). Every sign-in starts a new session.
  Last use is written at most once a minute. The CSRF token of a session is derived from its
  token (HMAC), so nothing more is stored.
- API tokens: `papiq_` plus 256 random bits, stored as SHA-256; scope `read` or `read_write`,
  a name, optional expiry, last use. Shown once. They survive a password change unless the
  caller (or the admin resetting it) asks to revoke them, and are refused while the account is
  deactivated.
- Accounts: admins create users (optionally with a password), change roles, reset passwords,
  deactivate, turn TOTP off and remove links to identity providers. Deleting a user needs that
  they own no documents and their drawers are empty; it removes their drawers, the shares to
  them and their sign-in data. The last active admin cannot be demoted, deactivated or deleted.
- The first admin: `PAPIQ_ADMIN_USERNAME` and `PAPIQ_ADMIN_PASSWORD[_FILE]` create an admin at
  API start while there is no admin at all. Afterwards they are ignored; they never change an
  existing account. A name taken by another user stops the start.
- OpenID Connect (optional, one provider; `core/services/oidc.py`, `adapters/outbound/oidc`):
  Authorization Code Flow with PKCE (S256) through Authlib over httpx2. State, nonce, verifier
  and the target path are sealed (AES-GCM, `PAPIQ_SECRET_KEY`) into a value for a short-lived
  cookie, which binds the flow to the browser that began it; it expires after ten minutes. The
  ID token is checked with joserfc: signature with an asymmetric algorithm the provider
  announces, issuer, audience (`azp` with several), expiry and issue time (one minute of
  leeway), nonce. Endpoints and keys come from the discovery document, whose issuer must equal
  `PAPIQ_OIDC_ISSUER`; keys are fetched again once for an unknown key id. Accounts are linked by
  issuer and subject, never by e-mail; a signed-in user links their account through the same
  flow. Unknown accounts are refused unless `PAPIQ_OIDC_AUTO_CREATE` creates a user named by
  `PAPIQ_OIDC_USERNAME_CLAIM` (a taken name is refused). No local TOTP is asked for: the
  provider handles that. Unlinking needs a local password. The redirect after the callback goes
  only to a path on this site.
- The cleanup job also removes ended sessions and failure counts past their window.

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

Receive → OCR → parse → classify → extract attributes → apply rules → file run as jobs
(`pipeline.step`); applying rules and filing are placeholders until M7.

| Step | Input | Derivatives (object store) | Outcome |
| --- | --- | --- | --- |
| OCR (`OcrStep`) | `originals/<sha256>` | `documents/<id>/archive.pdf` (PDF/A with text layer), `documents/<id>/preview.webp` (first page, 400 px wide) | uncertain if the archive is not PDF/A |
| Parse (`ParseStep`) | the archive PDF | `documents/<id>/content.md`, `documents/<id>/content.json` (Docling) | failed if no text was recognised |
| Classify (`ClassifyStep`) | `content.md`, master data | contact, document type, tags, document date (applied if checked) | see below |
| Extract attributes (`ExtractAttributesStep`) | `content.md`, the attributes of the type | attribute values (applied if checked) | see below |

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
- Deleting a document queues `documents.remove_files` in the same transaction: the worker
  removes its derivatives and its original, unless a document of any owner still has the same
  file (failures are repeated up to five times, from one minute, doubling). The removal and an
  upload of the same file lock the original's key (`UnitOfWork.lock`: a Postgres advisory lock,
  on SQLite the write lock); under that lock the upload stores the original again if it is
  gone, so no document is left without its original.
- A document with an uncertain step stops before filing with status `review` (yellow) and waits
  in its owner's inbox; `document.filed` comes only when it is green. Confirming it (see the
  inbox below) lets processing continue up to filing.
- Known gap: an original is stored before its document is created; if creating fails (e.g.
  the drawer's rights changed meanwhile), the object stays without a document. Removing such
  objects is left to a later cleanup of the object store.

## Classification

Two requests per document to an OpenAI-compatible chat completions endpoint
(`adapters/outbound/openai_compat`, `PAPIQ_LLM_*`): one for contact, document type, tags and
document date, one for the attributes of the document type. The answer must follow a JSON schema
(`response_format` `json_schema`, or `json_object` for providers without schema support); an
answer that does not fit is asked for once more, then the step fails (red). Without a
configured model both steps end uncertain (yellow): the owner fills in the fields.

The model only chooses and proposes; the core checks every field against facts, and only what
passes is applied (the same way as a change by the owner):

| Field | Check | Not passed |
| --- | --- | --- |
| Contact | Contacts are not sent; the proposed name is matched against all contacts (legal forms and punctuation ignored). Accepted if similar enough (`PAPIQ_CONFIDENCE_THRESHOLD`), named in the text and not ambiguous | an existing contact is suggested from `PAPIQ_CONTACT_SUGGEST_THRESHOLD` on, otherwise a new contact (only an admin can create it) |
| Document type | One of the existing types | new type suggested |
| Tags | Only existing tags are applied; proposed new tags are only logged | - |
| Document date, date attributes | A valid date that appears in the text (`31.03.2026`, `31.3.26`, `2026-03-31`, `31. März 2026`, `March 31, 2026`, ...) | |
| Amounts, numbers | The number appears in the text (German or English notation); the currency is shown as code, sign or word | |
| Text, link, choice, yes/no | The value, or the quoted passage, appears in the text | |
| Attributes of the type | A missing value makes the document yellow (global attributes may be missing) | |

The quoted passage must appear in the text as well. Every check and the raw answer are in the
processing log (`fields`, `answer`), so the proposal stays traceable after a correction; the
rules (M7) read them with `core.services.inbox.field_checks`.

The document text is sent between markers derived from its hash, and the model is told to treat
it as data. It can only answer with the fixed schema: there is no field for drawers, owners,
shares or actions, and additional fields make the answer unfit. Long documents are shortened to
`PAPIQ_LLM_INPUT_BUDGET` characters (beginning and end kept, the middle left out); with many
tags, at most `PAPIQ_LLM_MAX_TAGS` are listed, those named in the text first.

**Privacy.** The shortened document text and the names of document types, tags and attributes
go to the endpoint in `PAPIQ_LLM_BASE_URL`; the file, contacts and users do not. Every log entry
of the two steps names the endpoint host and the model. At start, a warning names every
configured model endpoint outside the local network (not loopback, private, link-local or a
local name), because document content leaves the system there.

**Ollama.** `PAPIQ_LLM_BASE_URL=http://<host>:11434/v1` (with `/v1`). Ollama does not take a
context size over this API and cuts longer prompts silently (often at 4,096 tokens); start it
with `OLLAMA_CONTEXT_LENGTH=8192` or more. Local models on a CPU are slow:
`PAPIQ_LLM_TIMEOUT` is per request, and the job lease allows twice that (one repeated request).

**Evaluation.** `python -m papiq.composition evaluate [--fake]` runs the synthetic documents in
`evaluation/` through both steps and writes a report; see [evaluation/README.md](evaluation/README.md).

## REST API

`python -m papiq.composition api` (with `PAPIQ_ROLE` `all` or `api`) serves
`adapters/inbound/rest` with Uvicorn on `PAPIQ_API_HOST`:`PAPIQ_API_PORT`. All routes are below
`/api/v1`; the OpenAPI document is `/api/v1/openapi.json`, the interactive docs `/api/v1/docs`.

Authentication: a session cookie from `POST /auth/login` or the OIDC callback, or a personal
API token as `Authorization: Bearer papiq_…` (a Bearer header wins over a cookie). Every route
depends on it except the five public ones (sign-in, OIDC start, info and callback, health); a
test calls every registered route without credentials and expects `401`. Requests that change
something need, with a session, the header `X-CSRF-Token` (from the sign-in answer or
`GET /auth/me`) and, with a token, the scope `read_write`; otherwise `403`. Changing sign-in
data needs a session, so a leaked API token cannot take over an account: the own password,
TOTP, tokens, sessions and links (`/auth/*`), and the admin endpoints that create users with a
password, reset passwords, turn TOTP off and remove links. Admins cannot reset their own
password or TOTP there; that goes through `/auth/*` with the current password or a code. Event streams check every 30
seconds that their session or token still holds and end otherwise.

The session cookie is `__Host-papiq_session`: HTTP-only, `Secure`, `SameSite=Lax`, `Path=/`, for
`PAPIQ_SESSION_MAX_AGE`; with `PAPIQ_COOKIE_SECURE=false` it is `papiq_session` without
`Secure`. Sign-in accepts JSON only, so a form on another site cannot sign anyone in.

| Endpoint | Purpose |
| --- | --- |
| `POST /auth/login` | Username, password, optional `code` or `recovery_code`; sets the cookie, returns the CSRF token |
| `POST /auth/logout`, `GET /auth/me` | Sign out; who is calling and how |
| `POST /auth/password` | Change the own password: other sessions end, `revoke_tokens` optional |
| `DELETE /auth/sessions` | End all other sessions |
| `POST /auth/totp`, `/totp/confirm`, `/totp/disable`, `/totp/recovery-codes` | TOTP |
| `GET/POST /auth/tokens`, `DELETE /auth/tokens/{id}` | Own API tokens |
| `GET /auth/oidc`, `/auth/oidc/login`, `/auth/oidc/callback`; `POST/DELETE /auth/oidc/link` | OpenID Connect |
| `GET /users`, `GET/PATCH/DELETE /users/{id}` | Accounts: list (others see active users' names), role and state, delete (admins) |
| `POST /users`, `POST /users/{id}/password`, `DELETE /users/{id}/totp`, `DELETE /users/{id}/oidc` | Create with password, reset password, turn TOTP off, remove links (admins, session only, not the own account) |
| `/contacts`, `/document-types`, `/tags`, `/attributes` (`GET`, `POST`, `GET/PATCH/DELETE /{id}`) | Master data: read by all, changed by admins, deleted only when unused. Attributes: name, choices and scope change, the data type does not; removing a used choice or narrowing the scope past documents with values is `409` |
| `GET/POST /drawers`, `GET/PATCH/DELETE /drawers/{id}`, `PUT/DELETE /drawers/{id}/shares/{user_id}` | Drawers and shares (owner) |
| `GET /documents` | Readable documents, newest first; filters `contact_id`, `document_type_id`, `tag_id`, `drawer_id`, `lane`; `limit`, `cursor` |
| `POST /documents` | Upload (multipart: `file`, optional `drawer_id`); `202` with `id`, `status_url` |
| `GET/PATCH/DELETE /documents/{id}` | Metadata and state with the caller's access; change (write access); delete (owner) |
| `POST /documents/{id}/move` | Into another drawer (owner, or an admin without read access) |
| `GET /documents/{id}/original`, `/archive`, `/preview` | Files (read access) |
| `GET /documents/{id}/log` | Processing log (owner) |
| `POST /documents/{id}/retry` | Repeat the failed step (owner) |
| `POST /documents/{id}/reprocess` | `{"from_step": "ocr"}`: process again from a step (owner) |
| `GET /inbox` | The caller's yellow and red documents, newest first, with their open steps and fields; `limit`, `cursor` |
| `GET /documents/{id}/review` | What the model proposed and how each field was checked (owner) |
| `POST /documents/{id}/confirm` | Decide the open fields (`changes` as with `PATCH`, `accept_suggestions`), then continue from `resume_at` (`apply_rules`, or `extract_attributes` after a type change) up to filing (owner); undecided fields: `422` with `open_fields` |
| `GET /events` | Server-sent events of the documents the caller may read; `?document_id=` |
| `GET /health` | Database reachable, bucket or storage directory usable; `200` or `503`, no authentication |

- Uploads are streamed into a temporary file and hashed on the way; the limit
  `PAPIQ_UPLOAD_MAX_SIZE` applies while receiving (`413`). The type is recognised from the
  content (PDF, JPEG, PNG, TIFF; otherwise `415`). A file the owner already has: `409` with
  `existing_document_id`.
- Errors are problem details (RFC 9457, `application/problem+json`) and documented per endpoint.
- Documents and drawers the caller may not see are `404` with the same answer as missing ones;
  lists, filters and pages only ever contain readable documents (the repository query follows
  the permission rule, and the service checks every result again). Yellow, red and unfinished
  documents are the owner's only.
- Every answer below `/auth` carries `Cache-Control: no-store` (CSRF tokens, TOTP secrets,
  recovery codes, API tokens), also errors and redirects. The OpenAPI document, the docs and
  `/health` stay public.
- Request bodies other than uploads are bounded by `PAPIQ_REQUEST_MAX_SIZE`, by
  `Content-Length` before anything is read and by the bytes received otherwise (`413`);
  FastAPI would otherwise read a JSON body of any size into memory.
- Downloads go through a temporary file and carry `X-Content-Type-Options: nosniff`,
  `Content-Security-Policy: sandbox` and `Cache-Control: private, no-store`.
- `401` (with `WWW-Authenticate: Bearer`), `429` (with `Retry-After`) and `502` (identity
  provider) are problems like all errors. The access log shows the OIDC callback without its
  query (code and state).
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
  `PAPIQ_RETENTION`, ended sessions and stale counts of failed sign-ins. The same loop runs
  the removal of a deleted document's files.
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
`PAPIQ_<NAME>` and `PAPIQ_<NAME>_FILE` is an error. In production, pass at least
`PAPIQ_SECRET_KEY_FILE` and `PAPIQ_ADMIN_PASSWORD_FILE` that way. The key and the admin
password in `.devcontainer/dev.env` are public; with secure cookies the start refuses the
development key.

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
| `PAPIQ_REQUEST_MAX_SIZE` | `1MiB` | Largest body of every other request (JSON); larger ones get `413` |
| `PAPIQ_FORWARDED_ALLOW_IPS` | unset; required with `PAPIQ_COOKIE_SECURE=true` | Reverse proxies whose `X-Forwarded-For` is trusted (comma-separated). Secure cookies mean a TLS-terminating proxy in front of Papiq: name its address, or the per-source throttle sees only the proxy. `*` only if the proxy sets the header itself, replacing what clients send |
| `PAPIQ_SECRET_KEY` | required for `all`, `api` | *secret*; 32 bytes base64 (`openssl rand -base64 32`); encrypts TOTP secrets. Keep it: without it, TOTP secrets cannot be read |
| `PAPIQ_ADMIN_USERNAME`, `PAPIQ_ADMIN_PASSWORD` | unset | The first admin, see Identity; password *secret*; set both or neither |
| `PAPIQ_SESSION_IDLE_TIMEOUT` | `P1D` | A session ends when unused this long |
| `PAPIQ_SESSION_MAX_AGE` | `P30D` | A session ends this long after sign-in; not shorter than the idle timeout |
| `PAPIQ_COOKIE_SECURE` | `true` | `false` only for development over plain HTTP |
| `PAPIQ_PUBLIC_URL` | unset | Where browsers reach Papiq (`https://papiq.example.org`); required with OIDC |
| `PAPIQ_OIDC_ISSUER`, `_CLIENT_ID`, `_CLIENT_SECRET` | unset | OpenID Connect; set all three or none; the issuer is https and compared exactly; secret *secret*. Register `<PAPIQ_PUBLIC_URL>/api/v1/auth/oidc/callback` as redirect URI |
| `PAPIQ_OIDC_SCOPES` | `openid profile email` | Must contain `openid` |
| `PAPIQ_OIDC_DISPLAY_NAME` | `Single sign-on` | Name of the provider for the sign-in page |
| `PAPIQ_OIDC_AUTO_CREATE` | `false` | Create a user at the first sign-in of an unknown provider account |
| `PAPIQ_OIDC_USERNAME_CLAIM` | `preferred_username` | ID token claim that names such a user |
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
| `PAPIQ_LLM_TEMPERATURE` | `0` | 0 to 2; 0 for repeatable answers |
| `PAPIQ_LLM_SEED` | unset | Passed to the endpoint, if set |
| `PAPIQ_LLM_TIMEOUT` | `300` | Seconds per request |
| `PAPIQ_LLM_RESPONSE_FORMAT` | `json_schema` | `json_object` for providers without JSON schema support |
| `PAPIQ_LLM_INPUT_BUDGET` | `12000` | Characters of document text per request |
| `PAPIQ_LLM_MAX_TAGS` | `200` | Tags listed per request |
| `PAPIQ_CONFIDENCE_THRESHOLD` | `0.9` | A field passes from this confidence on |
| `PAPIQ_CONTACT_SUGGEST_THRESHOLD` | `0.75` | An existing contact is suggested from this similarity on; not above the confidence threshold |
| `PAPIQ_EMBEDDING_BASE_URL`, `_MODEL` | unset | OpenAI-compatible endpoint; set both or neither |
| `PAPIQ_EMBEDDING_API_KEY` | unset | *secret* |
| `PAPIQ_EMBEDDING_TIMEOUT` | `60` | Seconds per request |

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
before every test). The CI job `integration` fails if any integration test skips.
