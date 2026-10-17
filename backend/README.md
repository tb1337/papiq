# papiq backend

Python package `papiq`, structured as ports and adapters.

```
src/papiq/
  core/            domain core, framework-free
    domain/        entities and value objects (document, space, contact, ...)
    ports/         interfaces the core needs (Python protocols)
    services/      use cases: ingest pipeline, rules, permissions
  adapters/
    inbound/       call the core: REST API, MCP server, worker, evaluation
    outbound/      implement ports: database, object store, search, LLM, OCR, parser
  composition/     composition root: reads configuration, wires adapters to ports, the
                   commands of `python -m papiq.composition`
```

Dependency rules, enforced by `uv run lint-imports`:

- `core` imports nothing from `adapters` or `composition`.
- `core.domain` imports nothing from `core.ports` or `core.services`.
- Inbound and outbound adapters never import each other.
- Only `composition` knows which adapter implements which port.

Every port gets an in-memory adapter for core tests and a contract test suite that every real
adapter must pass. Two adapters need no service and serve the memory container as well: the
pattern matcher (`regex`, a search with a time limit) and the template renderer (`liquid`).

## Core

- `core/domain`: users, spaces and shares, master data (contacts, document types, tags with
  their scopes and trees, `tag_tree.py`), field definitions and values, the document aggregate with the pipeline state machine and
  lanes, domain events, jobs and the permission rules. IDs are UUIDv7, timestamps are UTC.
- `core/ports`: repositories, processing log, outbox and job queue share one `UnitOfWork`, so
  a state change, its events and follow-up jobs are committed together (transactional outbox).
  `EventBus` delivers committed events at least once. The identity repositories (credentials,
  sessions, API tokens, external identities, failed sign-ins) are part of the unit of work too.
  Further ports: `ObjectStore`, `Clock`, `Ocr`, `DocumentParser`, `PreviewRenderer`,
  `PasswordHasher`, `SecretCipher`, `Totp`, `OidcProvider`, `LanguageModel`, `Embeddings`,
  `SearchIndex`, `PatternMatcher`, `WebhookSender`.
- `core/services`: use cases (users, spaces, master data, documents, pipeline, inbox,
  classification, rules, indexing, search, webhooks, maintenance). Each runs in one unit of work and checks the caller's rights.

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
  they own no documents and their spaces are empty; it removes their spaces, the shares to
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
creates a missing SQLite file. `python -m papiq.composition check-schema [--wait SECONDS]` only
looks (exit 0 if the schema is at the newest revision of this version; a database migrated by a
newer version fails at once); the image's worker waits with it for the API container's migration.
A schema change needs both an edit of `tables.py` and a new
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

A document goes through a graph of nodes (`core/domain/pipeline.py`, `GRAPH`; `GET /pipeline`
lists it), each node a job (`pipeline.node`): receive → OCR and pages side by side; OCR → parse →
the four signals side by side → judge → fields → apply rules → file; filing waits for the pages
too. Nodes that do not
need each other run as soon as their needs are met, so two workers may process one document at
once. `processing.nodes` of a document holds the state of every node of the current run
(`waiting`, `running`, `done` with its outcome, `skipped`) with start and end; the events
`document.node_started`, `document.node_deferred` (an attempt raised; the node waits for the
next one) and `document.node_completed` follow it. A node that fails stops the
document (red): nodes still running finish, nothing new starts. A retry repeats the failed
nodes, a reprocessing from node X repeats X and every node after it (from the judge also the
signals that ask no model, so new aliases, identifiers or a new model count); both start a new run, so
jobs of the old run are recognised as stale.

| Node | Input | Derivatives (object store) | Outcome |
| --- | --- | --- | --- |
| OCR (`OcrNode`) | `originals/<sha256>` | `documents/<id>/archive.pdf` (PDF/A with text layer); in the log, how well each recognised page was read (`readings`: words, unsure words below 60 % of Tesseract's confidence) | uncertain if the archive is not PDF/A, with the reason; OK with a note if the original's digital signature is not in the archive |
| Pages (`PagesNode`) | `originals/<sha256>` | `documents/<id>/pages/<n>.webp` (every page, 1200 px wide; an image narrower than that keeps its size, each frame of a TIFF is a page), `documents/<id>/preview.webp` (first page, 400 px wide); sets `page_count` | failed if the file cannot be read |
| Parse (`ParseNode`) | the archive PDF | `documents/<id>/content.txt` (the text as it is set), `documents/<id>/content.json` (its lines and words with boxes) | failed if no text was recognised |
| Signals (`signal_llm`, `signal_classifier`, `signal_neighbours`, `signal_facts`) | `content.txt`, master data, the classifier's model, the search index; `signal_facts` also the archive PDF | proposals in the log; `signal_facts` also `documents/<id>/codes.json` (the codes on the pages) | OK, also when abstaining (`abstained` names why); only `signal_llm` fails, after two unfit answers |
| Judge (`JudgeNode`) | the signals' entries in the log, `content.txt` | contact, document type, tags, document date (applied if green) | see Understanding |
| Fields (`FieldsNode`) | `content.txt`, the fields of the type and the global ones | field values and the title (applied if checked) | see Understanding |
| Apply rules (`ApplyRulesNode`) | `content.txt` (only if a rule looks at the text), the rules | space, contact, type, title, tags, fields | uncertain on conflicts, refused actions and forced reviews; see Rules |
| File (`FileNode`) | the space | - | uncertain if the owner may no longer file into the space (field `space`) |

- A node that raises is retried (`PAPIQ_NODE_MAX_ATTEMPTS`, delay `PAPIQ_NODE_RETRY_DELAY`,
  doubling); it waits meanwhile (`document.node_deferred`); after the last attempt the
  document goes red. A damaged or encrypted file
  (`UnprocessableDocumentError`) fails at once. The processing log records input, output,
  engine version, reason and duration of every run.
- OCRmyPDF (`adapters/outbound/ocrmypdf`) runs as a child process per document with a time
  limit (`PAPIQ_OCR_TIMEOUT`); on timeout or cancellation the process group is killed. The job
  lease is the longest limit of a node (OCR, reading with the vision model in
  `PAPIQ_VLM_TIMEOUT` if one is configured, or twice `PAPIQ_LLM_TIMEOUT`) plus two minutes, so a
  node normally keeps its claim; if it overruns (e.g. a slow S3 transfer), another worker takes
  the job over and the late result is discarded. A node interrupted by a stopping worker is
  released at once (`JobQueue.release`); that does not count against
  `PAPIQ_NODE_MAX_ATTEMPTS`.
- OCRmyPDF: `--skip-text` (pages with text are not recognised again), `--deskew`,
  `--rotate-pages`, `--output-type pdfa`, languages `PAPIQ_OCR_LANGUAGES`. A PDF with invisible
  text carries the layer of another OCR (a scanner's, an older archive's), which may be poor and
  says nothing of how well it read: it gets `--redo-ocr` instead (that layer is replaced,
  visible text kept, no deskewing, which OCRmyPDF does not allow then), and the log says
  `read_again`. A PDF whose text cannot be read gets `--force-ocr`: at least 30 % of a page's
  characters map to nothing (a font without a character map, as the ecoDMS PDF printer writes
  it, gives control characters), `--skip-text` would keep them and `--redo-ocr` keeps visible
  text. Every page is recognised from its image, the archive holds the pages as images (larger),
  and the log says why. Documents from before take the new reading when processed again from OCR. Images whose stated resolution gives an implausible page
  size (none, or 72 dpi from a phone) are scaled to the long edge of A4; transparency is put on
  white, CMYK is converted, all pages of a TIFF are kept. OCRmyPDF shares the CPU cores among
  `PAPIQ_WORKER_CONCURRENCY` jobs.
- A vision language model (`PAPIQ_VLM_*`, off without) reads a document only when a person
  asks for it (`POST /documents/{id}/vision`, owner or admin); nothing asks on its own. The
  document is processed again from OCR, and OCRmyPDF runs with `--force-ocr --deskew` and the
  plugin `ocrmypdf/plugin.py` as OCR engine: Tesseract still tells orientation and skew, every
  page image goes to the model at no more than 1.15 megapixels (so that the service does not
  scale it again), and the model answers with each line and its box in pixels of that image
  (prompt `read-1`, JSON schema; once more after an answer that does not fit). The words of a
  line share its box by their length; OCRmyPDF writes the text layer from the lines. Boxes in
  thousandths of the image drifted on a dense statement and on a photo, pixel boxes fit every
  line (issue #53). The archive holds the pages as images under the model's text, and that text
  counts as OCR: the checks are the usual ones, words the model marks `[unreadable]` count as
  unsure (`PAPIQ_POOR_READING_THRESHOLD`). The run keeps the request (`processing.vision`,
  revision 0021), so a retry asks the model again; reprocessing from `parse` and `reparse` keep
  the model's text (it is the archive's), reprocessing from `ocr` reads with Tesseract (the web UI asks first whether the model shall
  read again, and then calls `POST /documents/{id}/vision`). The archive's metadata names the
  reader: `/Creator` and `xmp:CreatorTool` say `Papiq, vision language model <model> (read-1)`. A page
  the model cannot read fails the node like the language model (`LanguageModelError`: retried,
  then red). The plugin gets its configuration in the environment of OCRmyPDF's process (the
  key apart, never logged) and leaves a record per page in a folder of ours. The log names
  model, endpoint and prompt (`output.vision`) and the tokens per call (`output.tokens`); the calls are kept like those of the
  language model (`exchanges/ocr.json`, the image as a placeholder). `GET
  /documents/{id}/reading` gives each recognised page and the advice to read with the model:
  `no_text` (red, the parse found no text) or `poor_page` (yellow or red, with a poorly read
  page); none once the model has read it.
- Two kinds of file get a second OCRmyPDF run. A digitally signed PDF is processed with
  `--invalidate-digital-signatures`: the original keeps its signature (originals never change),
  the archive has none, and the OCR entry of the log says so (outcome OK, `note`). A file whose
  PDF/A conversion fails outright (OCRmyPDF exit code 1, for example an unusual colour space) is
  written as a plain PDF (`--output-type pdf`); the node is uncertain with the reason. When
  Ghostscript itself refuses a construct of the file (overprint mode, a font with CID 0; exit
  code 10), the archive is a plain PDF as well and the reason comes from Ghostscript's
  messages. Such a document is yellow until its owner confirms it; the file cannot be made
  PDF/A without changing its content.
- The text is read from the archive's text layer as it is set (`adapters/outbound/pdfium/
  layout.py`, in a thread, milliseconds per page): every line as printed, its words in columns
  on the page's grid, so a label keeps its amount and columns of figures line up; a printout
  in a fixed-pitch font keeps its exact columns; a page whose text runs across it (a table
  printed sideways, a scan OCR read sideways) is read that way, text up the margin along its
  direction. Words end at the gaps between the characters, not at spaces (some PDFs set none).
  Pages are separated by a form feed on a line of its own. Headers and footers stay in the
  text (the identifiers of a letter's footer among them). A document parsed before 0.2 keeps
  Docling's Markdown (`content.md`) until its text is read again (`reparse`); it is read
  meanwhile. The structure is Papiq's own JSON:
  the pages with their size, the lines and words with boxes in fractions of the page as shown.
  It replaced Docling in 0.2: on the documents compared (`.idea/umsetzungsplan.md`) Docling
  dropped headers and footers, merged the rows of forms and needed a minute and 2 GB per
  document.
- Previews: PDFium (pypdfium2) renders, Pillow encodes WebP. PDFium is not thread-safe; all
  its use in a process shares one lock.
- Codes: `CodeReader` (`adapters/outbound/zxing`) renders each page of the archive in grey at
  300 dpi (an edge of at most 5000 px, at most 50 pages) and reads every format zxing-cpp knows
  (QR, Data Matrix, Aztec, PDF417, Code 128/39, EAN, ITF): format, text and box in fractions of
  the page; invalid and empty results are dropped, the same code on a page counts once. About
  0.1 to 0.3 s per page. A GiroCode (EPC069-12) is read as a credit transfer: payee, IBAN, BIC,
  amount, purpose (`core/domain/codes.py`).
- File metadata: `MetadataReader` (`adapters/outbound/pikepdf`) reads, on request, a PDF's
  version, PDF/A level, pages, document information and XMP packet (pikepdf), an image's
  format, size, resolution and EXIF without how its pixels are stored (Pillow); a damaged or
  encrypted file has none (`GET /documents/{id}/files`).
- Quotes on the page: the judge and the fields node look up every quote of the model in
  the text layer of the archive (`TextLocator`, PDFium): case, spacing, line breaks, hyphens at
  line ends and the marks of Markdown do not matter. Each field check keeps up to five places
  (page and one box per line, fractions of the page as shown, its rotation applied) and whether
  the quote shows the value itself or only leads to it (`quote_shows_value`); `/review`
  returns both. Documents processed before this have none until they are processed again.
- What the model nodes (`signal_llm`, `fields`, and `ocr` when the vision model read) send is
  kept beside the document, one file per node with every run
  (`documents/{id}/exchanges/<node>.json`: system and user message, the raw answer and the
  endpoint's whole usage report); owner and admins read it through
  `GET /documents/{id}/exchanges/{node}`. The files go with the document's other derivatives.
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
  the space's rights changed meanwhile), the object stays without a document. Removing such
  objects is left to a later cleanup of the object store.

## Tags

A tag has a scope (`TagScope`, fixed when it is made): `document` (a tag of documents, as before
0.3), `contact` (a contact group, "insurer"), `document_type` (a type group, "invoice" over
"medical bill") or `topic` (what a document is about with its contact, see Topics). Names are
unique per scope: a contact group may be named like a document tag.
Tags nest under a tag of the same scope (`parent_id`), at most five levels, without cycles
(`TagTree.check_parent`). Only the tag set is stored; its ancestors are derived (`TagTree`).

- **Documents** carry document tags only. A child and its parent set together leave the child
  (`TagTree.normalise`); a parent may be set alone. A document is found by a tag and by each of
  its ancestors.
- **Groups.** A contact and a document type are in any number of groups of their scope
  (`group_ids`); their documents inherit them with their ancestors. Groups are not stored at the
  document and not removed there.
- **Assignment.** A document tag is `auto` (the classification proposes and sets it) or `manual`
  (only a person or a rule sets it: statuses such as "to pay"); groups are always `manual`.
- **Shown.** Whether documents show a tag in their chips (default: on for document tags, off for
  groups); filters, search, rules and templates see it either way.
- **Filters** on a tag match the documents with it or a tag below it; on a group, those whose
  contact (type) is in it or a group below it. The core widens each tag or group by its
  descendants and hands the repository and the index sets ("one of these", all sets together,
  `DocumentFilter`), so no adapter walks a tree.
- **Counts** (`GET /tags?document_counts=true`): a document tag counts each document once that
  carries it or a tag below it; a group the documents whose contact (type) is in it or a group
  below it. A contact group and a topic also count their contacts (`contact_count`: in it or a
  group below it, enabling it or a topic below it), a type group its document types
  (`document_type_count`), each once.
- **Deleting** a tag works only without tags below it and while nothing uses it: no document,
  contact or type, and no rule (`409`, naming the rules; admins see all of them).
- **Renaming** a tag, nesting it elsewhere and changing the groups of a contact or type refresh
  the search index of the documents concerned (`search.refresh`).
- **Moving** a document tag or a group under another parent queues `tags.normalise`: what
  carries the tag (or one below it) and one now above it keeps only the one below, as if set
  anew (`master_data.normalise_tagged`). Documents are changed 200 per step, each an update
  of its own (`document.updated`, which refreshes its search entry), without touching lane or
  confirmation; a step holds the lock of the tag tree, reads the tree as it is then and queues
  the next step in the same commit (so a lease covers one step, and a tag moved back meanwhile
  is left alone). For a group the contacts or types (the index holds their groups with
  ancestors, which stay the same). A topic needs none: a document carries one, and a contact
  may enable a topic and one below it.

## Topics

A topic is a tag of scope `topic` (a global catalogue, nested like the other tags): what a
document is about with its contact, "Arbeit" or "Versicherung › Krankenversicherung" at an
insurance group that is employer and insurer at once.

- **A contact enables topics** (`Contact.topic_ids`, table `contact_topics`; admins, at most
  100). A topic and one below it may both be enabled. Disabling one is refused (`409`, naming
  it) while documents of that contact carry it; identifiers that point to it lose their topic.
- **An identifier** of a contact may point to one topic the contact enables
  (`contact_identifiers.topic_id`, checked by the contact). Found in a document, it is hard
  evidence for the pair of contact and topic. Training learns an identifier's topic only if
  all confirmed documents it stands in carry the same one.
- **A document carries one topic at most** (`documents.topic_id`), one its contact enables
  (`_access.checked_changes`). Another contact keeps it if it enables it, else it is cleared;
  without a contact there is no topic (`422`). An admin who gives a topic the contact does not
  enable enables it there in the same transaction; anyone else gets `422`. A topic cleared
  that way is no decision of the person's: change rules may set another one. A rule that sets
  another contact clears a topic it does not enable too, unless a person chose the topic: then
  the rule's contact is overruled.
- **Assignment.** An `auto` topic is proposed and set by the classification, a `manual` one
  only by a person or a rule. Topics are shown at the document by default (a square chip).
- **Filters, counts and deleting** work as for document tags: a topic finds the documents with
  it or one below it, counts each document once, and is deleted only without topics below it
  and while no document, contact, identifier or rule uses it (`409`). `topic_id` repeats in the
  list and the search: as a document has one topic, several mean one of them, each with the
  topics below it.

## Understanding

Four signals propose, the judge decides contact, topic, document type, tags and document date,
the fields node reads the fields of the decided type and the title
(`core/services/classification`, the rules in `core/domain/verdict.py`).

- **`signal_llm`**: one request to an OpenAI-compatible chat completions endpoint
  (`adapters/outbound/openai_compat`, `PAPIQ_LLM_*`, prompt `propose-3`) for sender and recipient,
  the topic (one of the automatic topics the listed contacts enable, or none; the prompt lists
  them at each of the at most 20 preselected contacts), type, tags (the automatic document
  tags), date, a title, the language, and the values of the fields of the type it chooses
  and of the global ones, each with the quoted passage. The answer must follow a JSON schema
  (`response_format` `json_schema`, or `json_object` for providers without schema support); an
  answer that does not fit is asked for once more, then the signal fails (red). Without a
  configured model it abstains, and the owner classifies.
- **`signal_classifier`**: TF-IDF of words and word pairs and a logistic regression per target
  (contact, topic, type, each automatic document tag), trained on the confirmed documents
  (`PAPIQ_CLASSIFIER`, see Classifier). It abstains without a trained model.
- **`signal_neighbours`**: the `PAPIQ_NEIGHBOURS` most similar confirmed documents the owner may
  read, by the vector of the start of the text against the search index; their contact, topic,
  type and automatic document tags come from the database. It abstains without embeddings or
  index.
- **`signal_facts`**: names and aliases of contacts standing whole in the text, identifiers of
  contacts (IBAN, VAT ID, customer number, personnel number, mail domain) with the topic they
  point to, unknown IBANs and VAT IDs. A personnel number stands after its label
  (`Personalnummer`, `Pers.-Nr.`, `Mitarbeiternummer`, `Employee ID`, ...) and is kept
  without leading zeros. It also reads the bar and QR codes on the archive's pages, once (`documents/<id>/codes.json`; the
  OCR node deletes it with a new archive, a reprocessing from the judge takes it): an
  identifier in a code counts as one in the text, the payee of a GiroCode as a name in the
  text, which proposes nothing alone. Reading the codes may fail; the signal warns and goes on
  without them. Which identifiers the document shows goes to `document_identifiers`, which
  tells on the confirmed documents of how many contacts an identifier stands.

A signal *names* a value it is sure of: the model, a contact whose name or alias its sender
matches at least `PAPIQ_CONFIDENCE_THRESHOLD` (legal forms and punctuation aside) with no other
close, or a type or tag of the lists; the classifier, its best class from
`PAPIQ_CLASSIFIER_THRESHOLD` on; the neighbours, a value at least two and more than half of them
carry, a contact only if no other contact is carried by two of them as well (neighbours split
between senders are alike by their form, a pay slip's employee for one, and name no sender). A
signal naming another value contradicts: always yellow, with every candidate and its
sources. The judge's rule:

| Field | Green (no contradiction) | Otherwise |
| --- | --- | --- |
| Contact | hard evidence (its identifier, or its name or alias in the text) and a signal names it; or the model's sender matches it from `PAPIQ_CONTACT_SUGGEST_THRESHOLD` on and the classifier or the neighbours name it. Never the model's recipient | yellow with ranked candidates; an identifier alone is a suggestion; an unknown sender a new contact (an admin creates it) |
| Topic (an automatic one the decided contact enables) | the contact is green, no signal or identifier names another topic, and an identifier of the pair stands in the text and a signal names it, or two signals name it | a suggestion, always under a contact that is only suggested; signals or identifiers naming different topics leave it empty with all candidates; a topic never makes a document yellow, and another contact clears one it does not enable |
| Document type | two signals name it, or only the model and no other signal covers it (the classifier was not trained on it, no confirmed document carries it) | yellow; a type outside the list is proposed as new |
| Tags (automatic document tags; one set by hand is never proposed) | set as a type is green, without the parent of a tag set | a suggestion (ids); tags never make a document yellow |
| Document date | a valid date that appears in the text, in a quoted passage that shows it (`31.03.2026`, `31.3.26`, `2026-03-31`, `31. März 2026`, `March 31, 2026`, ...) | suggested |

Without the model's proposal the judge is uncertain; it fails (red) only if the model answered
and no signal recognised anything.

The fields node reads the fields of the decided type and the global ones. If the model asked
for the type the judge decided, its values are taken over; otherwise (after a type correction,
or for a document from before the signals) it asks the model for the missing ones (prompt
`extract-4`). Each field is required or optional per type it is assigned to, a global one by its
own switch:

| Field | Check | Not passed |
| --- | --- | --- |
| Required field | its value is found and passes | yellow |
| Optional field | no value is fine (left empty); a value given must pass | yellow |
| Set by hand (`assignment: manual`) | never asked of the model; its value stays as the document has it; required, it needs a value a person or a rule set (checked by `apply_rules`, after the rules; confirming without a value is no decision) | yellow until a person enters it |
| Amounts, numbers | read as written (`2.111,68 €`, `1,234.56`, `-12,50`; an ambiguous one as the text shows it, else in the document's language) and found in the text; the currency shown as code, sign or word, or, where the text shows none at all (a pay slip's bare numbers), `PAPIQ_DEFAULT_CURRENCY` | |
| Dates | as the document date; a date field must differ from the document date | the date is suggested |
| Periods | read as written (`September 2026`, `09/2026`, `KW 38/2026`, `Q3 2026`, `2025`, `01.09.–30.09.2026`, `1. bis 30. September 2026`, a date as that day) or as `{start, end}`; plausible as a date; the quoted passage shows it as written or by its first and last day; of a value, the reading no other one holds counts (two are none) | without a year (it takes the document date's), or a single day equal to the document date: suggested |
| Text, link, choice, yes/no | the value, or the quoted passage, appears in the text (`ja`/`nein` read as yes or no) | |

The quoted passage must appear in the text as well, and for an amount, number, date, text,
link or period it must show the value itself: elsewhere in the text the same digits may stand for
anything, on a broken scan a lone fragment. An amount counts in the quote only as an amount,
with decimal places or a currency beside it (`620,00`, `620 €`, `620,-`; a bare `620` is none).
Without such a quote the value is only suggested, and so is a value on a page the OCR read
poorly (`PAPIQ_POOR_READING_THRESHOLD`; the page of its quote, or any recognised page if the
quote was not found): on a broken scan a number may be anything, however sure Tesseract was of
it. The document date follows the same rules. A field's description (up to 300
characters) goes into both prompts after its name and choices: it tells the model what belongs
in the field, whose name is often an internal one no document shows (`LA_Brutto`: the month's
gross total, not the base salary). The model is told to copy the quoted passage exactly as it
stands, numbers unchanged, and where the label and the value stand apart (a table, a form with
boxes), the line the value stands on; the check stays strict.

The title is the model's while the document still has its file name or the title the machine
gave it; a rule replaces the machine's title without making the document yellow. A document
type's title pattern is such a rule: a template (see Rules) that a global rule of the type
carries out, made, changed and deleted with the pattern and read-only otherwise
(`RuleOut.managed_by_document_type_id`, changes 409). It gives way to another rule that sets the
title (note `yielded`); a value it writes that the document lacks leaves the model's title;
confirming runs the rules again with the decided values. Every check with its
candidates and the raw answer is in the processing log (`fields`, `answer`), so a proposal stays
traceable after a correction; the rules read them with `core.services.inbox.field_checks`.

The document text is sent between markers derived from its hash, and the model is told to treat
it as data. It can only answer with the fixed schema: there is no field for spaces, owners,
shares or actions, and additional fields make the answer unfit. Long documents are shortened to
`PAPIQ_LLM_INPUT_BUDGET` characters, a run of spaces counted once (the columns of the text as it
is set cost a model little; beginning and end kept, the middle left out); with many
tags, at most `PAPIQ_LLM_MAX_TAGS` are listed, those named in the text first; of the contacts,
the 20 the text names most (with their aliases).

**Privacy.** The shortened document text, the names of document types, tags and fields, the
descriptions of types and fields, and the names of the contacts the text names go to the
endpoint in `PAPIQ_LLM_BASE_URL`; the file, other contacts and users do not. Every log entry of the two model nodes names the endpoint host and
the model. At start, a warning names every
configured model endpoint outside the local network (not loopback, private, link-local or a
local name), because document content leaves the system there.

**Ollama.** `PAPIQ_LLM_BASE_URL=http://<host>:11434/v1` (with `/v1`). Ollama does not take a
context size over this API and cuts longer prompts silently (often at 4,096 tokens); start it
with `OLLAMA_CONTEXT_LENGTH=8192` or more. Local models on a CPU are slow:
`PAPIQ_LLM_TIMEOUT` is per request, and the job lease allows twice that (one repeated request).

**Classifier.** One model for all users (`core/services/classifier.py`,
`adapters/outbound/sklearn`). Training examples are the documents whose metadata a person set or
confirmed (`confirmed_at`: confirming in the inbox, changing contact, type, topic, tags, date or
fields, taking a document over); green documents do not train. A change that only adds or
removes tags set by hand (statuses such as "to pay") confirms nothing: they say nothing about
contact and type (`documents.confirms`); with an automatic tag or any other confirming value
it confirms. A recurring job (`classifier.check`,
every ten minutes) queues the training (`classifier.train`) once `PAPIQ_CLASSIFIER_MIN_EXAMPLES`
documents are confirmed and no model exists, or `PAPIQ_CLASSIFIER_RETRAIN_AFTER` were confirmed
since the last one; `python -m papiq.composition train` and `POST /classifier/train` train at
once. Training also learns identifiers of contacts: one that stands in at least two confirmed
documents, all of one contact (the household's own IBAN stands on letters of many and is never
learned); a learned identifier an admin rejected stays rejected. The model is stored as `npz`
without pickle (`classifier/model-<n>.npz`, the latest three are kept) and predicted with numpy
alone, so a new scikit-learn version cannot break it. `GET /classifier` tells its state.

**After the update to 0.2.** Three things, in this order:

1. The search index gets the attribute `confirmed` the neighbours filter on. The migration
   raises the version of every confirmed document, so the worker's reconciliation at its start
   writes them again; vectors of unchanged text are kept, so this needs no embedding calls and
   no `reindex` (which would embed every document anew). Until it is through, the neighbours
   find fewer documents. Documents taken over before count as confirmed; the first training
   starts by itself once the worker runs. The same reconciliation writes every entry in the
   index's layout 2 (fields with a value, the title's sort key, periods in ISO), with the
   vectors it has.
2. 0.2 reads the text as it is set instead of Docling's Markdown. A document parsed before
   keeps its Markdown, which is read meanwhile, until `python -m papiq.composition reparse` (or
   `POST /pipeline/reparse`) has the worker read its text again; nodes, lane and metadata stay
   as they are. The search index then takes up the new texts, which embeds every document anew
   (with an embedding service elsewhere this takes a while: choose the time), and the
   classifier is trained on the new texts afterwards (`train`).
3. 0.2 reads the bar and QR codes on the pages. `python -m papiq.composition read-codes` has
   the worker read them for the documents from before and keep the identifiers each document
   shows (`document_identifiers`), so that the warning when attaching an identifier counts
   every confirmed document. It reads locally and sends nothing out; nodes, lane and metadata
   stay as they are. Documents taken over get their codes only this way: the signals abstain
   for them. Run it once `reparse` is through, which changes the text the identifiers are read
   from.

**After the update to 0.3.** Revisions 0024 to 0028 run at start; then four things:

1. "Drawer" is "space" everywhere, a break of the API: `/api/v1/spaces`, `space_id` (documents,
   upload, move, confirm, filters), the rule action `set_space`, the template variable
   `{{ space }}`. Revision 0024 rewrites what is stored (rule actions, the variable in rule
   templates and title patterns, open `document.filed` events, the processing log); a template
   that already uses `space` for something else stops it, and says which (rename it there
   first). A client that still sends `drawer_id` on upload files into the default space.
2. The worker's reconciliation at its start writes every entry in the index's layout 5 (the
   space, tags with their ancestors, the groups of contact and type, the topic), with the
   vectors it has: no embedding calls and no `reindex`. Until it is through, the search of
   other users misses documents shared with them.
3. Tags and fields from before are automatic document tags at the top level, shown on the
   document, and automatic fields. Mark status tags such as "to pay" "by hand only": the
   classification then never proposes them, and changing them alone confirms no document. Nest
   tags, put contacts and types into groups, enable topics at contacts and give identifiers
   their topic; the training learns topics (of documents and of learned identifiers) with its
   next run (`train` trains at once).
4. Logos of contacts come by upload. Fetching them from the contacts' websites is off: with
   `PAPIQ_LOGO_FETCH=true` a contact without a logo that gets a domain has it fetched, and
   `python -m papiq.composition fetch-logos` fetches them for the contacts from before
   (outgoing HTTPS to their domains only).

**Evaluation.** `python -m papiq.composition evaluate [--fake]` runs the synthetic documents in
`evaluation/` cold and trained and writes a report; see [evaluation/README.md](evaluation/README.md).

## Rules

Rules are data, not code (`core/domain/rules.py`, `core/domain/rule_engine.py`,
`core/services/rules`). A rule has a name, a priority (0 to 1000, default 100, higher first),
triggers, a tree of conditions and actions. Changing its content makes a new version; old
versions stay readable, so the processing log can refer to them. Enabling and disabling make no
version; deleting is a soft delete.

```json
{
  "name": "Telekom to household",
  "priority": 100,
  "triggers": ["ingest", "change"],
  "conditions": {"all": [
    {"field": "contact", "op": "is", "value": "<contact id>"},
    {"any": [{"field": "text", "op": "contains", "value": "Rechnung"},
             {"field": "text", "op": "matches", "value": "Kd\\.?-Nr"}], "not": true}
  ]},
  "actions": [
    {"type": "set_space", "space_id": "<space id>"},
    {"type": "add_tags", "tag_ids": ["<tag id>"]},
    {"type": "set_title", "template": "{{ contact }} {{ document_date | date: '%Y-%m' }}"}
  ]
}
```

- **Scope.** A user rule (any user) acts on its owner's documents with all actions and files
  only into spaces its owner may write to. A global rule (admins) acts on every document, but
  only with `add_tags`, `remove_tags`, `set_field` and `force_review`: nothing that changes
  who sees a document. Everyone reads the global rules; user rules are read by their owner and
  admins (`all_users=true`).
- **Triggers.** `ingest`: the pipeline node `apply_rules`, when a document arrives. `change`:
  a person changed the metadata of a document whose processing is complete.
- **Conditions.** Groups `all` or `any`, each may be negated with `"not": true`; at most 5 deep
  and 50 conditions. Fields and operators:

  | Field | Operators |
  | --- | --- |
  | `contact`, `document_type` | `is`, `in`, `present`, `missing` |
  | `topic` | `is`, `in`, `present`, `missing`; a topic matches the topics below it |
  | `tags` | `contains` (has this tag), `in` (has one of), `present`, `missing`; a tag matches the tags below it |
  | `contact_group`, `document_type_group` | as `tags`, over the groups of the document's contact (type) with their ancestors |
  | `channel` (Eingangskanal: `web`, `api`, `migration`) | `is`, `in` |
  | `text` | `contains` (normalised like the classification checks), `matches` (regular expression) |
  | `document_date` | `is`, `gt`, `lt`, `present`, `missing` |
  | `field` (with `field_id`) | by data type: text and link `is`, `in`, `contains`, `matches`; number, amount, date `is`, `gt`, `lt`; yes/no `is`; choice `is`, `in`; period `is`, `overlaps` (shares a day with a period), `gt` (starts after a date), `lt` (ends before a date); all `present`, `missing` |

  `matches` takes `case_sensitive` (default false). Patterns run with the `regex` package
  (`adapters/outbound/regex`) in a thread with a time limit (`PAPIQ_RULES_PATTERN_TIMEOUT`); a
  pattern that times out does not match and is logged. All patterns of one run on one
  document get two seconds together, a preview page twenty; the patterns left do not match. The text is the parsed text without
  markup, at most `PAPIQ_RULES_MAX_TEXT` characters; it is loaded only if a rule looks at it.
- **Actions.** `set_space`, `set_contact`, `set_topic`, `set_document_type`, `set_title`,
  `add_tags`, `remove_tags`, `set_field`, `force_review` (with a reason). `set_topic` is for
  user rules only; it is skipped (and noted) if the contact the document ends up with does not
  enable the topic. `add_tags` and `remove_tags` take document tags (also those set by hand);
  a tag above one the document keeps or gets is not added, a parent gives way to a child
  added. Saving a rule checks the scope of every tag it names (`422`).
- **Templates.** A title, the value of a text field and a review's reason are Liquid templates
  (`python-liquid`, `adapters/outbound/liquid`): `{{ contact }}`, `{% if field.la_brutto %}`,
  filters with `|`. They see `title`, `filename`, `original_filename`, `contact`, `topic`,
  `document_type`, `document_date`, `added`, `tags` (those set), `contact_groups` and
  `document_type_groups` (the groups the contact and the type are in, by name, sorted), `space`,
  `owner`, `channel`, `pages` and
  `field.<key>` (`FieldOut.key`, from the name: `LA_Brutto` is `la_brutto`; an amount has
  `.amount` and `.currency`, a period `.start` and `.end`), as the rules leave the document. A
  period writes itself in ISO 8601 by what it is (`Period.notation`): `2026-09`, `2026-W38`,
  `2026-Q3` (ISO has no short form for a quarter), `2026`, a day as `2026-09-15`, else its
  ends, `2026-09-01/2026-09-14`; `{{ field.x.start | datetime: format: 'MMMM yyyy' }}` writes
  it in words. `datetime`, `currency` and
  `decimal` format in `PAPIQ_TEMPLATE_LOCALE`, an amount writes itself as `3.650,00 EUR`. A
  template sees nothing but these values (no attributes of objects, no `include`), loops stop
  after 1,000 turns and output after 10,000 characters. Writing a missing value sets nothing
  (note `skipped`); `| default` and `{% if %}` make a part optional; a forced review holds with
  its reason as written. Saving checks the syntax and the values (422, `actions[2]: unknown
  value field.brutto`); single braces (`{contact}`, as before 0.2.0) are text like any other.
  Renaming a field rewrites the templates that use it (a new version); a field a template uses
  is not deleted (409). `POST /templates/preview` checks a template and renders it with a
  document the caller may read, and gives that document's values as a template writes them
  (`values`, for the template editor's list).
- **Evaluation.** All conditions see the state before any rule acts; a rule's action never makes
  another rule match in the same run. Contact, topic, type and tags the model set and no
  person confirmed are distrusted, and so are the groups of such a contact or type: a rule that
  matches only because of them does not file into a space that others see (shared or another
  user's); it is reported instead. Retroactively
  such a move is a conflict: it acts only where the person accepts it.
- **Combining.** A field set by one rule, or by several to the same value, is set. Different
  values are a conflict and nothing is set; priority never decides. A value a person decided
  in the current processing run is never replaced (the rule is logged as overruled): in the
  inbox, by a change, or the space chosen on upload or by moving the document; a value the model set and no person
  confirmed is not replaced either (conflict). Tags of all rules are combined; added by one and
  removed by another is a conflict. Reasons and notes name contacts, types, topics, spaces,
  tags and fields by their names and a field's value as written ("rules set different values
  ('Nord, Arbeit': 'Nord Versicherung', 'Süd': 'Süd AG')", "'Betrag': …", `12.50 EUR`,
  `2026-Q3`); the ids stay in the structured output (values, suggestions, effects), and only
  something that no longer exists is named by its id.
- **On arrival** conflicts, refused actions and forced reviews make `apply_rules` uncertain: the
  document waits in the inbox. Confirming it there (`POST /documents/{id}/confirm`, optionally
  with `space_id`) decides the open fields and continues to filing; a forced review the owner
  saw counts as answered. Filing checks once more that the owner may file into the space; if
  not, confirming needs a `space_id` (`422` with `open_fields: ["space"]`).
- **On a change** (`PATCH /documents/{id}`) rules are edge-triggered: a rule acts only if it
  holds after the change and did not hold before. Fields the person set in this change, or
  decided before in this processing run, are not touched. Nothing turns yellow: what cannot be applied is only reported, in the `rules`
  block of the answer (to the owner). If the rules file the document where an editor who made
  the change can no longer read it, the editor's answer is only `{"id": …, "access": null}`.
  `POST /documents/{id}/dry-run` shows the same without storing anything.
- **Retroactively.** `POST /rules/{id}/apply/preview` lists the documents the current version
  would change, newest first, with changes and conflicts (a page looks at 200 documents at
  most). `POST /rules/{id}/apply` pins a version and applies it to the selected documents (at
  most `PAPIQ_RULES_APPLY_MAX_DOCUMENTS`) in the background, job `rules.apply`, 25 documents per
  job; conflicts only where accepted (`accept_conflicts`), forced reviews do not act. A user rule
  is applied by its owner to their documents, a global rule by anyone to the documents they may
  write to; rights are checked again per document. Progress: `GET /rule-applications/{id}`.
- **References.** Contacts, types, tags, fields and spaces a rule names must exist (`404`),
  spaces must be writable for the owner (`403`). Deleting one of them, or removing a choice a
  rule uses, disables the rules that use it with a reason; enabling checks again. A tag a rule
  uses is not deleted (`409`). Deleting a user
  removes their rules.
- **Log.** Every run is a processing log entry of node `apply_rules` with the rules and versions
  checked, matches, effects and notes. `model_version`: `rules` (the pipeline node),
  `rules:change` (after a change; it also records what the person changed), `rules:apply`
  (retroactive), `person` (a confirmation), `person:space` (a space chosen on upload or by
  moving). Later runs read from the log which values a person
  decided and which the model set.

## Search

Hybrid search in Meilisearch: full text and meaning (vectors) in one query. Without
`PAPIQ_MEILISEARCH_URL` there is no search; without `PAPIQ_EMBEDDING_BASE_URL` it works on words
only. `GET /api/v1/documents/search?q=…` takes the filters of `GET /documents`, `limit`,
`offset` (at most 1000 hits can be reached) and `semantic_ratio` (0 words only, 1 meaning only;
default `PAPIQ_SEARCH_SEMANTIC_RATIO`). Each hit carries the document as `GET /documents/{id}`
does, a score and a snippet whose matches are marked. `semantic` in the answer tells whether the
meaning took part.

- **The index follows the documents.** The indexing service subscribes to the document events
  (`search.index`) and queues one job per change; it writes the document and reads it back, up to
  three rounds, so a change made meanwhile is not lost. A failed job repeats after 30 s,
  doubling up to an hour, ten times; the last attempt writes the words without vectors. Renaming
  a contact, a type or a tag, nesting a tag elsewhere and changing the groups of a contact or
  type queue the documents concerned: the index holds names for the search by words. A reconciliation compares the index with the database and queues what is
  missing, stale or gone: every `PAPIQ_SEARCH_RECONCILE_INTERVAL` and at every start of a worker.
  If the embedding endpoint is down, the document is written at once with the vectors it has
  and the job repeats for the new ones. If Meilisearch loses its data, the adapter sets the
  index up again; restart the worker or call `POST /search/reindex` to fill it without waiting
  for the next reconciliation.
- **Layout of an entry.** Each entry carries the layout it was written in (`LAYOUT` in
  `core/domain/search.py`). Layout 2 adds the ids of the fields with a value (`field_ids`), the
  title's sort key and periods in ISO 8601 beside the German words (`2026-W38` and `KW 38 2026`);
  layout 3 the space instead of the drawer; layout 4 the tags with their ancestors and the groups
  of contact and type with theirs (`contact_group_ids`, `document_type_group_ids` filterable,
  their names searchable); layout 5 the topic with its ancestors (`topic_ids` filterable,
  `topics` searchable, the topic's path in the header of the first section's embedding). The
  reconciliation writes older entries again and keeps their vectors, since the embedded text
  does not change (no document had a topic before): no embedding calls.
- **Sorting.** `sort` comes first among Meilisearch's ranking rules, so relevance breaks ties,
  also with vectors; document date, arrival and the title's sort key are sortable. Without
  `sort` (or with `relevance`), the hits come by relevance.
- **Rebuild at any time.** `POST /search/reindex` (admins) or `python -m papiq.composition
  reindex` builds `<index>-rebuild` from the database and the object store while the active
  index keeps serving, swaps it in and reconciles what changed meanwhile. The index holds no
  information that is not in the database.
- **Rights.** The index query contains the rights (own documents in any lane; green documents of
  spaces the caller owns or that are shared with them). Every hit is checked against the
  database once more and the filter applied again, so a withdrawn share ends the search for that
  document at once, before the index has changed; a page can therefore hold fewer items than
  `limit`. Documents still in processing are in the index, for their owner only.
- **Sections.** The text (at most `PAPIQ_SEARCH_MAX_TEXT` characters) is cut into sections of
  about `PAPIQ_SEARCH_CHUNK_SIZE` characters, at most `PAPIQ_SEARCH_MAX_CHUNKS`; each gets a
  vector, and the first one starts with title, contact, type and the tags set (not their
  ancestors: nesting a tag elsewhere costs no embedding). `PAPIQ_SEARCH_MAX_CHUNKS=1`
  gives one vector per document. A vector is made again only when model or section texts change.
- **Embeddings.** The query is embedded at request time (`PAPIQ_SEARCH_EMBED_TIMEOUT`); if the
  endpoint is down or slow, the search falls back to words (`semantic: false`).
  The model is `snowflake-arctic-embed2` with `PAPIQ_EMBEDDING_QUERY_PREFIX=query:` (compared with
  `bge-m3` and `qwen3-embedding:0.6b` in `evaluation/search/reports`).
  `PAPIQ_EMBEDDING_DIMENSIONS` is the length of the vectors and required with Meilisearch and an
  embedding endpoint; vectors of another length are refused (the query then goes by words).
  Changing the model means a rebuild. Some models want a prefix for queries
  and documents (`PAPIQ_EMBEDDING_QUERY_PREFIX`, `PAPIQ_EMBEDDING_DOCUMENT_PREFIX`).
- **Limits.** Meilisearch ranks the documents nearest in meaning even if no word matches, so
  a hybrid search always returns hits; there is no threshold yet. A rebuild occupies one worker
  loop for its time. On a CPU the embedding of the sections dominates the indexing cost;
  `evaluate-search` measures it for a model.
- **Health.** `GET /health` includes `search` when it is configured. If only the search is
  down, the status is `degraded` and the answer stays `200`: the API works without it.

**Evaluation of the embedding model.** `python -m papiq.composition evaluate-search [--fake]
[--models a,b] [--ratios 0,0.5,1]` indexes the documents of the evaluation set with each model in
a temporary Meilisearch index and runs the queries of `evaluation/search/queries.json`; see
[evaluation/README.md](evaluation/README.md).

## Icons, colours and logos

- **Icons.** Tags of every scope and document types carry the name of a Lucide icon
  (`heart-pulse`: lower-case letters and digits in words joined by hyphens, at most 64
  characters; `appearance.checked_icon`). Only the form is checked: the web UI brings the icons
  in its own build and shows a stand-in for a name it does not know.
- **Colours.** A tag carries one of the twelve colours of the palette (`gray`, `red`, `orange`,
  `amber`, `lime`, `green`, `teal`, `sky`, `blue`, `violet`, `fuchsia`, `pink`; the web UI tunes
  each for light and dark) or an own one as `#rrggbb` (stored in lower case).
- **Logos.** A contact shows its logo, else its initials. The file is a WebP of at most 128 px
  at `contacts/<id>/logo.webp` (`objects.logo_key`); the contact keeps where it came from
  (`uploaded` or `fetched`), the domain of a fetched one and when it was stored
  (`Contact.logo`), and the domains whose fetch found none (`logo_failure`). It goes with its
  contact. File and contact change under one lock per contact (`logos.logo_lock`), the
  contact's row before the file: another change of the contact meanwhile fails the version
  check before the file is replaced, and the step starts again (three times).
  - *Upload* (admins): a PNG, JPEG or WebP image of at most 1 MiB, recognised by content; SVG is
    refused (`415`: it can carry scripts). The port `ImageNormaliser` (Pillow,
    `adapters/outbound/pillow`) reads PNG, JPEG, WebP and ICO only, checks the size before it
    decodes (at most 4096 × 4096 pixels; of an ICO every picture by its own header) and writes
    a WebP keeping transparency. An uploaded logo is never replaced by a fetched one.
  - *Fetch* (`PAPIQ_LOGO_FETCH=true`; off by default, Papiq then never asks the web on its own):
    the worker's job `contacts.logo` asks the contact's own website for its icon, the port
    `LogoFetcher` (`adapters/outbound/logos`, httpx2). It is queued when a contact without a
    logo, or with a fetched one, gets a domain among its identifiers (entered by hand, learned
    by training, attached from a document; a logo found there replaces the fetched one, none
    found leaves it), by an admin (`POST /contacts/{id}/logo/fetch`; all its domains in turn,
    also one that failed; may replace a fetched logo) and for the contacts without a logo with
    `python -m papiq.composition fetch-logos`. A job that runs while `PAPIQ_LOGO_FETCH` is off
    (switched off after it was queued) ends at once with a note, without waiting or retries;
    the button answers `503` then. It reads the page of the domain (or of `www.`),
    the apple-touch-icon, `<link rel="icon">` with sizes and the web app manifest (with the
    standard library's `html.parser`), and `/favicon.ico`, and takes the largest raster icon of
    at least 64 px (at most four tried). The domains whose fetch finds none are noted and
    tried again only on request; a new domain is tried at once. No logo service of a third party is
    asked: it would learn whom one deals with.
  - *Protection against SSRF.* The domains come from documents. Every request and every
    redirect resolves the name and goes on only if every address it has is public (Python's
    `is_global`, and not shared 100.64.0.0/10, unique local, site-local, with a zone,
    IPv4-compatible, 6to4 or Teredo; a mapped or NAT64 IPv4 address counts as that address),
    then connects to the address it checked with the name as `Host` and for TLS (SNI and the
    certificate), so that a second answer of the name server cannot lead elsewhere (DNS
    rebinding). HTTPS on port 443 only, at most three redirects (each checked again), at most
    1 MB and 10 s per request (30 s per fetch), only the content types asked for, no encoding
    but gzip (decoded within the limit), no cookies, no proxies or `.netrc` from the
    environment (`trust_env=False`).

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
| `/contacts`, `/document-types`, `/tags`, `/fields` (`GET`, `POST`, `GET/PATCH/DELETE /{id}`) | Master data: read by all, changed by admins, deleted only when unused. Contacts and types take `group_ids`, contacts `topic_ids` (the topics they enable) and identifiers with an optional `topic_id`. Fields: name, description, choices and scope change, the data type does not; removing a used choice or narrowing the scope past documents with values is `409`. The lists take `document_counts=true` (and `all_users` for admins): each item with `document_count`, the documents in the caller's reach that have it (a field: a value for it; a tag: see Tags), counted in one query (tags in a few: the parents' counts and the groups' come apart) |
| `GET /tags` | The tags in the order of their trees (each after its parent, siblings by name; document tags, contact groups, type groups, topics), or of one `scope`; each with `scope`, `parent_id`, `path` (the names from the top), `assignment`, `shown`, `icon`, `color`. `POST` takes `scope` (default `document`), `parent_id`, `assignment` and `shown` (by scope unless given), `icon` and `color`; `PATCH` name, parent (`null`: the top), assignment, `shown`, `icon` and `color` (`null` removes them), never the scope. Document types take an `icon` too |
| `PUT/DELETE /contacts/{id}/logo`, `GET /contacts/{id}/logo` | A contact's logo (see Icons, colours and logos): upload (multipart `file`, admins; `413` above 1 MiB, `415` for another kind than PNG, JPEG or WebP, `422` if it cannot be read), remove (admins), read (everyone; WebP with `Cache-Control: private, max-age=86400`, the only answer of the API a browser may keep: `ContactOut.logo.url` carries the version). `ContactOut` has `logo` (source, domain, time, URL) and `logo_failure` (the domains whose fetch found none, the time of the last) |
| `POST /contacts/{id}/logo/fetch` | Fetch the logo from the contact's website (admins, `202`); `503` without `PAPIQ_LOGO_FETCH`, `409` with an uploaded logo, `422` without a domain |
| `PUT /document-types/{id}/fields` | The fields of a document type, each required or optional, set from the type in one transaction (admins); answers its fields. A global field is `422`; taking off a field that has values on its documents or that the type's title pattern uses is `409` |
| `POST /contacts/{id}/identifiers` | Attach one identifier to a contact, entered by hand (admins), optionally with a `topic_id` the contact enables; the others stay; `409` if another contact has it by hand. `PATCH` replaces the whole list |
| `GET/POST /spaces`, `GET/PATCH/DELETE /spaces/{id}`, `PUT/DELETE /spaces/{id}/shares/{user_id}` | Spaces and shares (owner) |
| `GET /documents/search` | Search: `q`, the filters of `GET /documents`, `sort` (`relevance`, the default, or a key of `GET /documents`; relevance breaks ties), `limit`, `offset`, `semantic_ratio`; hits with snippet, `estimated_total`, `next_offset`, `semantic` |
| `POST /search/reindex` | Rebuild the search index in the background (admins); `202` |
| `GET /documents` | Readable documents; `sort`: `-created_at` (the default, newest first), `created_at`, `document_date`, `-document_date`, `title`, `-title` (without case and accents), documents without a date last either way; filters `contact_id`, `document_type_id`, `tag_id` (with the tags below it), `contact_group_id` and `document_type_group_id` (the document's contact or type in the group or one below it; these three repeat, all must match), `topic_id` (the topic or one below it; repeats, one of them must match), `space_id`, `lane`, `field_id` (a value for the field), `all_users` (admins); `limit`, `cursor` (the last document's id; its sort value is looked up, `422` if it is gone) |
| `POST /documents` | Upload (multipart: `file`, optional `space_id`, optional `channel=migration`; otherwise the channel is `web` with a session, `api` with a token); `202` with `id`, `status_url` |
| `GET/PATCH/DELETE /documents/{id}` | Metadata and state with the caller's access, `channel`, `topic_id` (see Topics); change (write access; the answer has the change rules' `rules` report for the owner); delete (owner or admin) |
| `POST /documents/{id}/dry-run` | A change as with `PATCH`, with the change rules, nothing stored: the result and the differences |
| `POST /documents/{id}/move` | Into another space (owner, or an admin without read access) |
| `GET /documents/{id}/original`, `/archive`, `/preview`, `/pages/{number}` | Files (read access); pages from 1 to `page_count` |
| `GET /documents/{id}/text` | The text as it is set (plain text, pages apart by form feeds) in pieces: `offset`, `limit`; `409` while not parsed |
| `GET /documents/{id}/files` | The files as stored (read access): when the document was added and changed, its original's name and type, and for the original and the archive (null while there is none) size, SHA-256 and metadata: a PDF's version, PDF/A level, pages, document information and XMP; an image's format, size, resolution and EXIF |
| `GET /documents/{id}/facts` | What the document shows of its contacts (read access): names and identifiers with the contact they belong to now and their spans in the text (characters, as `/text` counts them); the codes on its pages, each once for its format and text, with the pages and boxes where it stands (a box per copy), the GiroCode read, their identifiers and the amount fields of the same value; for admins, on the documents of how many other contacts an identifier stands; `409` while not parsed |
| `GET /documents/{id}/log` | Processing log (owner or admin) |
| `POST /documents/{id}/retry` | Repeat the failed nodes (owner or admin) |
| `POST /documents/{id}/reprocess` | `{"from_node": "ocr"}`: process again from a node, with every node after it (owner or admin) |
| `POST /documents/{id}/vision` | Process again from OCR with the vision language model reading every page (owner or admin); `409` without a model |
| `GET /documents/{id}/reading` | How the archive's text was read: by the vision model or not, each recognised page, the advice to read with the model (owner or admin) |
| `GET /pipeline` | The graph: every node and what it needs, in graph order; the vision model, if one is configured; whether logos are fetched (`logo_fetch`) |
| `POST /pipeline/reparse` | Read the text of every document again in the background (admins, after the update to 0.2); `202` with `queued` |
| `GET /inbox` | The caller's yellow and red documents, newest first, with their open nodes and fields; `limit`, `cursor` |
| `GET /documents/{id}/review` | What the model proposed and how each field was checked (owner or admin) |
| `POST /documents/{id}/confirm` | Decide the open fields (`changes` as with `PATCH`, `accept_suggestions`, `space_id`), then continue from `resume_at` (`apply_rules`, or `fields` after a type change) up to filing (owner or admin); undecided fields: `422` with `open_fields` |
| `GET/POST /rules`, `GET/PUT/PATCH/DELETE /rules/{id}`, `GET /rules/{id}/versions`, `/versions/{number}` | Rules: list (`scope`, `include_disabled`, `all_users` for admins), create, change (new version), enable or disable, delete; see Rules |
| `POST /rules/{id}/apply/preview`, `POST /rules/{id}/apply`, `GET /rule-applications/{id}` | Apply a rule to existing documents: preview, start (`202`), progress |
| `GET /events` | Server-sent events of the documents the caller may read, for admins also `classifier.trained`; `?document_id=` |
| `GET/POST /webhooks`, `GET/PATCH/DELETE /webhooks/{id}` | Webhooks: the caller's own (admins read everybody's, `?owner=`); create (the answer shows the secret once), change, switch on or off, delete; see Webhooks |
| `POST /webhooks/{id}/secret`, `POST /webhooks/{id}/test`, `GET /webhooks/{id}/deliveries` | Renew the secret (the old one signs 24 hours longer), send a test request, the delivery log (`before`, `limit`) |
| `GET /health` | Database, bucket or storage directory and, if configured, the search index reachable; `200` (`ok`, or `degraded` if only the search is down) or `503`, no authentication; not written to the access log |

- Uploads are streamed into a temporary file and hashed on the way; the limit
  `PAPIQ_UPLOAD_MAX_SIZE` applies while receiving (`413`). The type is recognised from the
  content (PDF, JPEG, PNG, TIFF; otherwise `415`). A file the owner already has: `409` with
  `existing_document_id`.
- Errors are problem details (RFC 9457, `application/problem+json`) and documented per endpoint.
  Filing into a space without write access (upload, move, confirm, a rule that sets the space)
  is `403` with `space_id`, the space that was refused.
- Documents and spaces the caller may not see are `404` with the same answer as missing ones;
  lists, filters and pages only ever contain readable documents (the repository query follows
  the permission rule, and the service checks every result again). Yellow, red and unfinished
  documents are the owner's only.
- Every answer below `/auth` carries `Cache-Control: no-store` (CSRF tokens, TOTP secrets,
  recovery codes, API tokens), also errors and redirects. The OpenAPI document, the docs and
  `/health` stay public. Every other answer of the API is `no-store` too, but for a contact's
  logo (`GET /contacts/{id}/logo`, successful answers only), which keeps its own
  `private, max-age=86400`: its URL changes with the logo, so lists need not load it again.
- Request bodies other than uploads (documents, logos) are bounded by `PAPIQ_REQUEST_MAX_SIZE`, by
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
  clients fetch the state. `document.deleted` goes to the streams of the users who could read the
  document when it was deleted (the event carries their ids; the stream message does not).
  `classifier.trained` (a new model: `number`, `examples`) goes to the active admins' streams
  that do not follow one document; webhooks cannot choose it. One API instance
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
- Memory: OCRmyPDF takes about 150 MB per job, the worker itself about 0.5 GB; reading the
  text needs little. The OCR is bound by the CPU: OCRmyPDF shares the cores among
  `PAPIQ_WORKER_CONCURRENCY` jobs, so more loops than cores gain nothing.
- The cleanup (`maintenance.cleanup`, one job with a fixed dedup key; it goes before pipeline
  steps when due) runs every
  `PAPIQ_CLEANUP_INTERVAL` and removes finished jobs and delivered events older than
  `PAPIQ_RETENTION`, webhook delivery log rows of that age, ended sessions, stale counts of
  failed sign-ins and the previous secrets of webhooks whose grace period
  (`PAPIQ_WEBHOOK_SECRET_GRACE`) is over. The same loop runs the removal of a deleted
  document's files.
- Rule applications (`rules.apply`) run 25 documents per job, then queue the next job, so
  pipeline nodes do not wait behind them.
- After a tag moved, `tags.normalise` leaves what carries it and a tag now above it the one
  below (after rule applications; see Tags); a row changed meanwhile fails the version check,
  and the job runs again (five tries, waiting from a minute, doubling).
- With `PAPIQ_LOGO_FETCH`, the job `contacts.logo` fetches the logo of a contact (after rule
  applications, before training; see Icons, colours and logos). It asks the network outside
  any transaction and is repeated only for other errors than the website's (three tries).
  Without it, a job queued before ends at once (failed, with the note that fetching is off).
- `PAPIQ_WEBHOOK_CONCURRENCY` further loops deliver webhooks (see Webhooks); a receiver that
  does not answer holds up deliveries, never the pipeline.
- SIGTERM or SIGINT: no new jobs; running jobs may finish within
  `PAPIQ_WORKER_SHUTDOWN_TIMEOUT`, then they are cancelled and their jobs released to run again
  at once. Finally the database engine and the S3 client are closed.
- Failed event deliveries are repeated with doubling delay from one second up to an hour, at
  most `PAPIQ_EVENTS_MAX_ATTEMPTS` times. Several dispatchers under the same subscriber name
  may deliver an event twice (at least once is allowed).
- Every loop runs its units of work one after another, never nested, as SQLite requires.

## Webhooks

A user subscribes to document events and Papiq sends a signed `POST` to a URL (n8n, Home
Assistant, any service). Webhooks belong to the user who created them; the API is under
`/webhooks` (create, list, change, switch off, delete, renew the secret, test, delivery log).
Admins may do everything with every webhook: read it and its log, change, delete, test, renew the
secret (which then shows to them, once). Nobody sees a secret again after it was shown. What an
admin reads includes the target URL (which may be a capability URL) and, in the log, the document
IDs and event types of that user's deliveries.
At most `PAPIQ_WEBHOOKS_PER_USER` (20) per user.

**Events.** `document.received`, `document.node_started`, `document.node_deferred`, `document.node_completed`, `document.lane_changed`,
`document.filed`, `document.updated`, `document.deleted`, or `*` for all, also future ones. A
webhook only hears of documents its owner may read, as with the event stream: the right is
checked when the event is queued and again before every attempt, and a user who lost access in
between gets nothing (the log shows `dropped`). The body carries no lane, so a receiver
that wants the state of the document fetches it; `document.filed` is not sent while the document
is still being processed. `document.deleted` goes to those who could read the document
when it was deleted.

**The request.** `POST <url>`, `Content-Type: application/json`, `User-Agent: Papiq/<version>`,
and a body that names the event only; the receiver fetches details through the API with a token
of its own:

```json
{"id": "01999d5e-...", "type": "document.filed", "occurred_at": "2026-10-08T12:00:00Z",
 "document_id": "01999d5e-..."}
```

`id` is the event id: it is the same in every repeated attempt, so a receiver can recognise
events it has handled. Delivery is at least once and not in order; sort by `occurred_at`. The
test request (`POST /webhooks/{id}/test`) has `type: "webhook.test"` and `document_id: null`;
it goes out at once, to whatever address the webhook names, so a user may send at most ten per
minute (`429` with `Retry-After` beyond that; counted per API process).

**Signature** ([Standard Webhooks](https://www.standardwebhooks.com), so ready-made verification
libraries work). Three headers: `webhook-id` (the event id), `webhook-timestamp` (Unix seconds
of this attempt) and `webhook-signature`, `v1,<base64>` of the HMAC-SHA256 over
`<webhook-id>.<webhook-timestamp>.<raw body>` with the secret (`whsec_` and 32 bytes in Base64;
the key is the Base64-decoded part after the prefix). Right after the secret was renewed the
header holds two signatures separated by a space, one per secret; accept the request if one
matches. A receiver should:

1. read the raw body (not a re-serialised JSON),
2. recompute the signature and compare with `hmac.compare_digest`,
3. refuse a `webhook-timestamp` older than five minutes (protection against replay; every
   attempt has a fresh timestamp),
4. skip event ids it has handled already.

```python
import base64, hashlib, hmac, time


def verify(secret: str, headers: dict[str, str], body: bytes) -> bool:
    if abs(time.time() - int(headers["webhook-timestamp"])) > 300:
        return False
    key = base64.b64decode(secret.removeprefix("whsec_"))
    content = f"{headers['webhook-id']}.{headers['webhook-timestamp']}.".encode() + body
    expected = base64.b64encode(hmac.new(key, content, hashlib.sha256).digest()).decode()
    signatures = [part.split(",", 1)[1] for part in headers["webhook-signature"].split()]
    return any(hmac.compare_digest(expected, signature) for signature in signatures)
```

The same on the command line, for a body saved as `body.json` without changes:

```sh
KEY=$(printf %s "${SECRET#whsec_}" | base64 -d | od -An -vtx1 | tr -d ' \n')
{ printf '%s.%s.' "$WEBHOOK_ID" "$WEBHOOK_TIMESTAMP"; cat body.json; } \
  | openssl dgst -sha256 -mac HMAC -macopt hexkey:"$KEY" -binary | base64
# compare the output with the part after "v1," in webhook-signature
```

**Answers and repetition.** Any `2xx` answer is delivered. No answer (unreachable, timeout after
`PAPIQ_WEBHOOK_TIMEOUT`, certificate refused), `5xx`, `408`, `425` and `429` are repeated, up to
`PAPIQ_WEBHOOK_MAX_ATTEMPTS` attempts with `PAPIQ_WEBHOOK_RETRY_DELAY` doubling up to an hour
(30 s, 1, 2, 4, 8, 16, 32, 60, 60 minutes: about three hours). `3xx` and every other `4xx` are
final: redirects are never followed, since the signature would not hold for another target.
After `PAPIQ_WEBHOOK_DISABLE_AFTER` (20) deliveries given up in a row Papiq switches the webhook
off (`active: false`, `disabled_reason: failing`); switching it on again clears the count.
A receiver that is down delays the deliveries behind it, since the `PAPIQ_WEBHOOK_CONCURRENCY`
loops wait for it up to the timeout per attempt; the pipeline is not affected.

**The log** (`GET /webhooks/{id}/deliveries`, newest first) has one row per attempt: time,
event, `outcome` (`delivered`, `retrying`, `gave_up`, `dropped`), HTTP status, duration, error
and the time of the next attempt. The receiver's answer is neither stored nor shown. Rows are
removed after `PAPIQ_RETENTION`.

**Secrets.** Papiq generates the secret and shows it once, in the answer to creating a webhook
or renewing its secret. It is stored encrypted with `PAPIQ_SECRET_KEY` (the worker needs the key
too, to sign). Renewing keeps the old secret signing next to the new one for
`PAPIQ_WEBHOOK_SECRET_GRACE` (24 hours), so the receiver can switch without a gap; renewing again
ends that at once.

**Targets.** `http` and `https` to any host and port, including the own network. `https`
certificates are verified; there is no switch against that (use `http` or a valid certificate).
Anyone who may create webhooks can make the server send `POST`s to addresses it can reach and
see from status and error whether something answers; the body is fixed and the answer is not
shown.

## MCP

The API serves the Model Context Protocol (Streamable HTTP, stateless, JSON answers) at
`/api/v1/mcp`, so AI clients can search documents, read them and correct their metadata.
`PAPIQ_MCP_ENABLED=false` removes it. It is not part of the OpenAPI document.

Authentication: a personal API token (`POST /auth/tokens`; the web UI will offer it later) as
`Authorization: Bearer papiq_…`; cookies do not count. Without a valid token the answer is `401`
(`WWW-Authenticate: Bearer`). A client sees exactly what its token's user sees. The scope works
as in REST: `read` may use every tool but `update_metadata`.

| Tool | Does |
| --- | --- |
| `search` | `query`, `limit` (1 to 25), `offset`, optional `contact`, `document_type`, `tags` (names of document tags, all of them; a tag finds the documents with a tag below it too), `topics` (names of topics, one of them; a topic finds those with a topic below it too): hits with `id`, `title`, `score`, `snippet` (plain text), contact, type, topic, tags, date, lane, `access`; `next_offset`, `semantic` |
| `get_document` | `id`: metadata with contact, topic, type, tags and fields by name, lane and processing status |
| `get_text` | `id`, `offset`, `limit` (characters, at most `PAPIQ_MCP_TEXT_MAX`, 20,000): a piece of the document's text as it is set, `next_offset` to continue. The text is content from outside; the tool description tells clients not to follow instructions in it |
| `update_metadata` | `id` and any of `title`, `contact`, `topic`, `document_type` (name; `null` removes; a topic the contact enables), `tags` (names of document tags, the complete list, replaces), `document_date` (`null` removes), `fields` (field name to value; `null` removes). Needs a `read_write` token and write access; the owner's change rules run as for `PATCH /documents/{id}` and the result shows them. If the rules filed the document where the caller can no longer read it, the result has only `id` and `access: null` |
| `list_tags` | The tags with `scope` and `path`, in the order of their trees, optionally of one `scope`; document tags name the tags of the other tools, topics the `topic` of `update_metadata` |

Names are matched regardless of case; an unknown name is an error, nothing is created. A
document the caller may not read is "not found", with the same text as a missing one.

Set up Claude Code (use a `read` token if the client should only read):

```sh
claude mcp add --transport http papiq https://papiq.example.org/api/v1/mcp \
  --header "Authorization: Bearer papiq_..."
```

Other clients take the same URL and header; the MCP Inspector
(`npx @modelcontextprotocol/inspector`) is handy to try it out: transport *Streamable HTTP*,
the URL, and the header `Authorization`. Behind a reverse proxy the path needs no special
handling; requests are plain `POST`s with JSON bodies, limited by `PAPIQ_REQUEST_MAX_SIZE`.

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
Durations must be positive.

The table names every variable (a test checks it against `settings.py`). **Role** says which
service reads it: `API`, `Worker` or `both`; a container with `PAPIQ_ROLE=all` needs them all,
separate API and worker containers only theirs (the connection and storage settings in both).
Durations are seconds (`30`, `1.5`) or ISO 8601 (`PT1H`, `P7D`).

| Variable | Role | Default | Meaning |
| --- | --- | --- | --- |
| `PAPIQ_ROLE` | both | `all` | `all`, `api` or `worker`: which services run (the image's s6 scripts read it) |
| `PAPIQ_LOG_FORMAT` | both | `json` | `json` or `console` |
| `PAPIQ_LOG_LEVEL` | both | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR`, `CRITICAL` |
| `PAPIQ_DB_TYPE` | both | `sqlite` | `sqlite` or `postgres` |
| `PAPIQ_DB_SQLITE_PATH` | both | `data/papiq.db`; image: `/data/papiq.db` | SQLite only; local volume (WAL), never a network share |
| `PAPIQ_DB_HOST` | both | unset | required for `postgres` |
| `PAPIQ_DB_PORT` | both | `5432` |  |
| `PAPIQ_DB_NAME` | both | unset | required for `postgres` |
| `PAPIQ_DB_USER` | both | unset | required for `postgres` |
| `PAPIQ_DB_PASSWORD` | both | unset | *secret*; required for `postgres` |
| `PAPIQ_STORAGE_TYPE` | both | `filesystem` | `filesystem` or `s3` |
| `PAPIQ_STORAGE_PATH` | both | `data/objects`; image: `/data/objects` | filesystem only |
| `PAPIQ_S3_ENDPOINT_URL` | both | unset | required for `s3`, e.g. `http://garage:3900` |
| `PAPIQ_S3_REGION` | both | `us-east-1` |  |
| `PAPIQ_S3_BUCKET` | both | unset | required for `s3` |
| `PAPIQ_S3_ACCESS_KEY_ID` | both | unset | *secret*; required for `s3` |
| `PAPIQ_S3_SECRET_ACCESS_KEY` | both | unset | *secret*; required for `s3` |
| `PAPIQ_S3_PATH_STYLE` | both | `true` | Path-style addressing (Garage needs it) |
| `PAPIQ_API_HOST` | API | `0.0.0.0` | Where the API listens |
| `PAPIQ_API_PORT` | API | `8000` |  |
| `PAPIQ_UPLOAD_MAX_SIZE` | API | `100MiB` | Largest upload; bytes or with unit (`50MB`, `1GiB`); a proxy's body limit must allow it |
| `PAPIQ_REQUEST_MAX_SIZE` | API | `1MiB` | Largest body of every other request (JSON); larger ones get `413` |
| `PAPIQ_UI_DIR` | API | unset; image: `/opt/papiq/ui` | The built web UI (`web/build`), served below `/ui`; `/` leads there. Must contain `index.html`; unset serves the API only |
| `PAPIQ_FORWARDED_ALLOW_IPS` | API | unset; required with `PAPIQ_COOKIE_SECURE=true` | Reverse proxies whose `X-Forwarded-For` is trusted (comma-separated). Secure cookies mean a TLS-terminating proxy in front of Papiq: name its address, or the per-source throttle sees only the proxy. `*` only if the proxy sets the header itself, replacing what clients send |
| `PAPIQ_SECRET_KEY` | both | required | *secret*; 32 bytes base64 (`openssl rand -base64 32`); encrypts TOTP and webhook secrets, the worker decrypts the latter to sign. Keep it: without it they cannot be read. The development key is refused with secure cookies |
| `PAPIQ_ADMIN_USERNAME` | API | unset | The first admin, created at API start while there is no active admin; set together with the password |
| `PAPIQ_ADMIN_PASSWORD` | API | unset | *secret*; see above |
| `PAPIQ_SESSION_IDLE_TIMEOUT` | API | `P1D` | A session ends when unused this long |
| `PAPIQ_SESSION_MAX_AGE` | API | `P30D` | A session ends this long after sign-in; not shorter than the idle timeout |
| `PAPIQ_COOKIE_SECURE` | API | `true` | `false` only for development over plain HTTP |
| `PAPIQ_PUBLIC_URL` | API | unset | Where browsers reach Papiq (`https://papiq.example.org`); required with OIDC |
| `PAPIQ_OIDC_ISSUER` | API | unset | OpenID Connect: set issuer, client id and client secret together or none; the issuer is `https` and compared exactly. Register `<PAPIQ_PUBLIC_URL>/api/v1/auth/oidc/callback` as redirect URI |
| `PAPIQ_OIDC_CLIENT_ID` | API | unset | see above |
| `PAPIQ_OIDC_CLIENT_SECRET` | API | unset | *secret*; see above |
| `PAPIQ_OIDC_SCOPES` | API | `openid profile email` | Must contain `openid` |
| `PAPIQ_OIDC_DISPLAY_NAME` | API | `Single sign-on` | Name of the provider on the sign-in page |
| `PAPIQ_OIDC_AUTO_CREATE` | API | `false` | Create a user at the first sign-in of an unknown provider account |
| `PAPIQ_OIDC_USERNAME_CLAIM` | API | `preferred_username` | ID token claim that names such a user |
| `PAPIQ_WEBHOOKS_PER_USER` | API | `20` | Webhooks a user may have |
| `PAPIQ_WEBHOOK_SECRET_GRACE` | API | `P1D` | How long the old secret signs after renewing; the cleanup drops it afterwards |
| `PAPIQ_WEBHOOK_TIMEOUT` | Worker | `10` | Seconds per delivery attempt (1 to 120) |
| `PAPIQ_WEBHOOK_MAX_ATTEMPTS` | Worker | `10` | Attempts per delivery (1 to 50) |
| `PAPIQ_WEBHOOK_RETRY_DELAY` | Worker | `30` | Seconds before the second attempt, doubling after, at most an hour |
| `PAPIQ_WEBHOOK_DISABLE_AFTER` | Worker | `20` | Deliveries given up in a row until a webhook is switched off |
| `PAPIQ_WEBHOOK_CONCURRENCY` | Worker | `4` | Deliveries at the same time |
| `PAPIQ_MCP_ENABLED` | API | `true` | Serve MCP at `/api/v1/mcp` |
| `PAPIQ_MCP_TEXT_MAX` | API | `20000` | Characters one `get_text` call returns at most (1,000 to 1,000,000) |
| `PAPIQ_WORKER_CONCURRENCY` | Worker | `2` | Jobs at the same time, see Worker |
| `PAPIQ_WORKER_POLL_INTERVAL` | Worker | `1` | Seconds between looks for due jobs |
| `PAPIQ_WORKER_SHUTDOWN_TIMEOUT` | Worker | `30` | Seconds running jobs get to finish on SIGTERM |
| `PAPIQ_NODE_MAX_ATTEMPTS` | Worker | `3` | Attempts of a pipeline node that raises |
| `PAPIQ_NODE_RETRY_DELAY` | Worker | `30` | Seconds before the second attempt, doubling after |
| `PAPIQ_EVENTS_POLL_INTERVAL` | Worker | `1` | Seconds between outbox dispatches |
| `PAPIQ_EVENTS_MAX_ATTEMPTS` | Worker | `10` | Delivery attempts of an event per subscriber |
| `PAPIQ_CLEANUP_INTERVAL` | Worker | `3600` | Seconds between cleanups |
| `PAPIQ_RETENTION` | Worker | `P7D` | Age of finished jobs, delivered events and webhook log rows to remove |
| `PAPIQ_OCR_LANGUAGES` | Worker | `deu+eng` | Tesseract languages, joined by `+` |
| `PAPIQ_OCR_TIMEOUT` | Worker | `600` | Seconds per document for OCR |
| `PAPIQ_MEILISEARCH_URL` | both | unset | Without it there is no search (the API searches, the worker indexes) |
| `PAPIQ_MEILISEARCH_API_KEY` | both | unset | *secret*; the master key or a key with access to the index |
| `PAPIQ_MEILISEARCH_INDEX` | both | `papiq-documents` | The active index; a rebuild fills `<name>-rebuild` and swaps it in |
| `PAPIQ_MEILISEARCH_TIMEOUT` | both | `30` | Seconds per request |
| `PAPIQ_MEILISEARCH_TASK_TIMEOUT` | Worker | `120` | Seconds a write waits for its task |
| `PAPIQ_SEARCH_LOCALES` | both | `deu+eng` | Languages of the documents, ISO 639-3 codes joined by `+` (index settings) |
| `PAPIQ_SEARCH_SEMANTIC_RATIO` | API | `0.5` | Weight of the meaning against the words when a request does not say |
| `PAPIQ_SEARCH_EMBED_TIMEOUT` | API | `5` | Seconds to wait for the embedding of a query, then words only |
| `PAPIQ_SEARCH_MAX_TEXT` | Worker | `200000` | Characters of text per document in the index |
| `PAPIQ_SEARCH_CHUNK_SIZE` | Worker | `1500` | Characters per section with a vector |
| `PAPIQ_SEARCH_MAX_CHUNKS` | Worker | `8` | Sections with a vector per document |
| `PAPIQ_SEARCH_RECONCILE_INTERVAL` | Worker | `21600` | Seconds between comparisons of index and database |
| `PAPIQ_SEARCH_REBUILD_TIMEOUT` | Worker | `21600` | Seconds a rebuild may take |
| `PAPIQ_LLM_BASE_URL` | Worker | unset | OpenAI-compatible endpoint with version path (`http://ollama:11434/v1`); set together with the model or neither |
| `PAPIQ_LLM_MODEL` | Worker | unset | see above |
| `PAPIQ_LLM_API_KEY` | Worker | unset | *secret* |
| `PAPIQ_LLM_TEMPERATURE` | Worker | `0` | 0 to 2; 0 for repeatable answers; `none` sends none (for models that reject one, such as Claude Sonnet 5.5) |
| `PAPIQ_LLM_SEED` | Worker | unset | Passed to the endpoint, if set |
| `PAPIQ_LLM_TIMEOUT` | Worker | `300` | Seconds per request (`600` on a CPU) |
| `PAPIQ_LLM_RESPONSE_FORMAT` | Worker | `json_schema` | `json_object` for providers without JSON schema support |
| `PAPIQ_LLM_INPUT_BUDGET` | Worker | `12000` | Characters of document text per request (a run of spaces counts once) |
| `PAPIQ_LLM_MAX_TAGS` | Worker | `200` | Tags listed per request |
| `PAPIQ_EMBEDDING_BASE_URL` | both | unset | OpenAI-compatible endpoint; set together with the model or neither (the worker embeds sections, the API embeds queries) |
| `PAPIQ_EMBEDDING_MODEL` | both | unset | see above |
| `PAPIQ_EMBEDDING_API_KEY` | both | unset | *secret* |
| `PAPIQ_EMBEDDING_TIMEOUT` | both | `60` | Seconds per request |
| `PAPIQ_EMBEDDING_DIMENSIONS` | both | unset | Length of the vectors (`snowflake-arctic-embed2`, `bge-m3`: 1024); required with Meilisearch and an embedding endpoint |
| `PAPIQ_EMBEDDING_QUERY_PREFIX` | API | unset | Put before queries, for models that ask for it (`query:`) |
| `PAPIQ_EMBEDDING_DOCUMENT_PREFIX` | Worker | unset | Put before document sections, for models that ask for it |
| `PAPIQ_VLM_BASE_URL` | both | unset | OpenAI-compatible endpoint of a vision language model that reads a document's pages when a person asks for it (`POST /documents/{id}/vision`); the page images go there. Set together with the model or neither; off without. The API names model and endpoint in `GET /pipeline` |
| `PAPIQ_VLM_MODEL` | both | unset | see above: a model that takes images and gives each line's box in pixels (measured with Claude Sonnet 5.5) |
| `PAPIQ_VLM_API_KEY` | Worker | unset | *secret* |
| `PAPIQ_VLM_TIMEOUT` | Worker | `1800` | Seconds for reading a whole document with the model (one page at most 300); the job lease grows with it |
| `PAPIQ_CONFIDENCE_THRESHOLD` | Worker | `0.9` | A field passes from this confidence on |
| `PAPIQ_CONTACT_SUGGEST_THRESHOLD` | Worker | `0.75` | An existing contact is suggested from this similarity on; not above the confidence threshold |
| `PAPIQ_POOR_READING_THRESHOLD` | Worker | `0.08` | Share of unsure words (below 60 % of Tesseract's confidence) from which a page the OCR recognised counts as poorly read: a value on it is only suggested. The default lies between good scans (up to 7 %) and broken ones (from 9 %) of a first sample; calibration in M18 |
| `PAPIQ_DEFAULT_CURRENCY` | both | `EUR` | The currency of an amount in a document that shows no currency at all (ISO 4217), and of the `currency` filter in templates where they name none |
| `PAPIQ_CLASSIFIER` | both | `scikit-learn` | The classifier trained from confirmed documents; `none` turns it off |
| `PAPIQ_CLASSIFIER_MIN_EXAMPLES` | both | `20` | Confirmed documents before the first model is trained |
| `PAPIQ_CLASSIFIER_RETRAIN_AFTER` | both | `25` | Documents confirmed since the last model before it is trained again |
| `PAPIQ_CLASSIFIER_THRESHOLD` | Worker | `0.8` | A prediction this sure names its contact, type or tag |
| `PAPIQ_NEIGHBOURS` | Worker | `5` | Similar confirmed documents that vote on a new one (needs the search index with embeddings); `0` turns them off |
| `PAPIQ_LOGO_FETCH` | both | `false` | Fetch the logos of contacts from their own websites (worker; the API offers the button): HTTPS to public addresses only, see Logos. Off, Papiq never asks the web on its own |
| `PAPIQ_RULES_PATTERN_TIMEOUT` | both | `0.2` | Seconds a regular expression of a rule may run per text or value (worker: rules; API: previews) |
| `PAPIQ_RULES_MAX_TEXT` | both | `200000` | Characters of text rules look at (1,000 to 10,000,000) |
| `PAPIQ_RULES_APPLY_MAX_DOCUMENTS` | both | `1000` | Documents per retroactive rule application (1 to 100,000) |
| `PAPIQ_TEMPLATE_LOCALE` | both | `de` | Locale in which templates of rules and title patterns write dates and amounts (`datetime`, `currency`, `decimal`, an amount field as `3.650,00 EUR`); Babel's names, such as `de`, `en_US` |

## Tests

- `tests/unit` (marker `unit`): no external services.
- `tests/integration` (marker `integration`): need Postgres, Garage or Meilisearch or the OCR
  programs (Tesseract, Ghostscript). Which services apply follows the
  `PAPIQ_` variables; a test skips itself if what it needs is not configured or not reachable.
  The devcontainer provides all of them.
- Sample files (scan, text PDF, photo, empty page, damaged PDF, Office file) are in
  `tests/samples`, generated by `make_samples.py`.

The markers are applied by directory, so new tests only need to be placed in the right folder.

`tests/contracts` holds the contract suites (classes such as `UnitOfWorkContract`). An adapter's
test module subclasses each suite as `Test...` and provides the adapter fixture
(`uow_factory`, `event_bus_factory`, `object_store`, `clock`, `ocr`, `parser`,
`preview_renderer`, `webhook_sender`); see
`tests/unit/adapters/memory/test_contracts.py`.

The SQL adapter runs the contract suites and `tests/sql_suite.py` (types, concurrency,
migrations) on SQLite in `tests/unit/adapters/sql` (a migrated database file per test) and on
Postgres in `tests/integration/adapters/sql` (a database of its own per test session, emptied
before every test). The CI job `integration` fails if any integration test skips.
