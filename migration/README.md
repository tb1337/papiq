# papiq migration

Command-line client that takes over a Paperless-ngx archive into Papiq. It reads Paperless
through its REST API and writes into Papiq through the Papiq API, with an admin's API token. It
uses no back doors, so a complete migration also proves the API is complete. It is a separate
uv project (`papiq_migration`, Python 3.13, `httpx2` is its only dependency) and not part of the
Papiq image.

## What is taken over

| Paperless-ngx | Papiq |
| --- | --- |
| Users | users by user name; missing ones are created without password, role `user`, inactive ones deactivated |
| Correspondents, document types, tags | contacts, document types, tags, matched by name (case-insensitive) |
| Custom fields | global attributes: string/longtext → text, url → link, date → date, boolean → yes/no, integer/float → number, monetary → amount, select → choice |
| ASN, notes | attributes `ASN` (number) and `Notizen` (text, one paragraph per note with date and author) |
| Document date | document date |
| Owner | owner (no owner, or a deleted one: the admin who runs the migration) |
| Permissions | a drawer of the owner for every combination of readers and writers (groups resolved to users), named `Geteilt: anna (schreiben), bob (lesen)`; without extra permissions the owner's default drawer |
| File | the original (not Paperless' archive PDF) |

Not taken over, and listed or counted in the report: storage paths, `documentlink` fields, the
date added, history, share links, saved views, workflows, mail rules and accounts, documents in
Paperless' trash, further versions of a file, matching rules of master data, tag hierarchy and
inbox flag. Users who are inactive in Paperless are created active, because they may own
documents or receive shares, and deactivated when the migration is through. Files Papiq does not accept (anything but PDF, JPEG, PNG, TIFF) and
documents in Paperless' trash are listed with the reason.

Documents are uploaded with `channel=migration`, the owner and their metadata
(`POST /documents` fields `owner` and `metadata`, admins only). Papiq's classification and
attribute steps apply the metadata instead of asking a language model; OCR, parsing and the rules
run as usual, so the lane of a document is what the pipeline decides.

## Use

```sh
cd migration && uv sync
export PAPIQ_MIGRATION_PAPERLESS_URL=http://paperless:8000
export PAPIQ_MIGRATION_PAPERLESS_TOKEN=...     # or ..._TOKEN_FILE=/path
export PAPIQ_MIGRATION_PAPIQ_URL=http://papiq:8000
export PAPIQ_MIGRATION_PAPIQ_TOKEN=...         # an admin's API token, scope read_write

uv run papiq-migration plan      # trial run: reads, writes nothing, reports what would happen
uv run papiq-migration run       # takes everything over; Ctrl-C stops cleanly, run again to continue
uv run papiq-migration verify    # compares Paperless and Papiq object by object
```

Options (all commands): `--state` (SQLite file, default `migration-state.sqlite`), `--report-dir`
(default `migration-reports`), `--currency` (for amounts without one, default `EUR`),
`--concurrency` (documents at a time, default 4), `--limit N` (the first N documents by id),
`--pipeline-timeout`. `verify --rehash` downloads the originals again and compares their hashes.
API keys come from the environment (or a file) only and are never written to the state or the
reports.

The state file maps every Paperless object to its Papiq object and records how far each document
got. An interrupted run continues where it stopped; a second run creates nothing twice; a state
file belongs to one Paperless and one Papiq (if Papiq is reset, delete the state file, too).
`verify` compares the SHA-256 in Papiq with the checksum Paperless keeps for the original;
`--rehash` downloads the originals again and hashes them. Reports (`plan`, `run`, `verify`, each as `.md` and
`.json`) list every Paperless object with its Papiq counterpart or the reason it has none.
Exit codes: 0 fine, 1 failures or deviations, 2 cannot go on (wrong key, unreachable server),
130 stopped.

## Checks

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest
```

The tests run the client against stand-ins for both APIs (`papiq_migration/fake_paperless.py`,
`tests/fake_papiq.py`). The tests against the real API (in memory) are in the backend:
`backend/tests/unit/adapters/rest/test_migration_flow.py`.
