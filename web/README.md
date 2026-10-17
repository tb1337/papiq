# papiq web

Web UI: a single-page app on top of the Papiq REST API, served by the API under `/ui`. It talks to
the API only (`/api/v1`), through a TypeScript client generated from the API's OpenAPI document.

Stack: SvelteKit 3 (Svelte 5, static adapter, no server-side rendering), Tailwind CSS 4,
shadcn-svelte (bits-ui), Paraglide JS (English and German), openapi-fetch, CodeMirror 6 (templates),
Vitest. Fonts (Geist, Geist Mono) are part of the build; no CDN.

## Commands

Run in `web/` (Node 24, pnpm as pinned in `package.json`):

```sh
pnpm install
pnpm dev             # Vite on :5173, /api proxied to the API on :8000
pnpm lint && pnpm format:check
pnpm check           # svelte-check
pnpm test            # Vitest (CI)
pnpm build           # static app in build/
pnpm e2e             # Playwright smoke test against a running API (local only)
```

The smoke tests (`tests/e2e`) run against the API serving the built UI. `smoke.spec.ts` creates a
user through the admin of the environment, signs in with password and TOTP, navigates, reloads and
signs out. `areas.spec.ts` signs in as that admin (without a second factor), opens every area
without a console error (a CSP violation is one) and creates and removes a contact, a space, a
webhook and an API token:

```sh
pnpm build
# backend/: the API with PAPIQ_UI_DIR=../web/build and PAPIQ_COOKIE_SECURE=false
PAPIQ_ADMIN_USERNAME=… PAPIQ_ADMIN_PASSWORD=… pnpm e2e
```

`PAPIQ_E2E_URL` (default `http://127.0.0.1:8000`) names the API, `PAPIQ_E2E_CHROMIUM` a Chromium
of another Playwright version instead of `pnpm exec playwright install chromium`.

For `pnpm dev`, start the API in `backend/` with `uv run python -m papiq.composition api` and
`PAPIQ_COOKIE_SECURE=false` (plain HTTP). Open http://localhost:5173/ui/.

## PDF viewer and CSP

The viewer uses the legacy build of `pdfjs-dist` (the normal one needs `Map.getOrInsertComputed`,
missing in older browsers). The worker is bundled; the plugin `vite-pdfjs.js` serves and emits
wasm, standard fonts and CMaps under `/ui/pdfjs/`.
The Content-Security-Policy (`vite.config.ts`) allows `worker-src 'self'` and `style-src-attr 'unsafe-inline'` (pdf.js sets style attributes);
scripts, style elements and images stay `'self'` only: pdf.js draws on a canvas and creates no
blob or data URLs, previews come from the API, the TOTP QR code is inline SVG (checked in the
browser with a PDF holding a JPX image, M13).

## Template editor

Templates of rules and title patterns are edited with CodeMirror (`src/lib/templates/codemirror.ts`),
loaded with `import()` where a template is shown. The colours, the place of a completion and
where a value or filter goes are plain functions in `liquid.ts`, which the rule view uses too.
The editor sits in a shadow root: there CodeMirror adopts a constructed style sheet, which the
CSP lets through, instead of a `<style>` element, which it blocks. The page's styles do not reach
in (the editor's theme holds its colours), and bits-ui's focus trap does not see focus inside, so
a listener keeps `focusin` from the editor away from it. Component tests get a plain text area in
place of the editor (`tests/setup.ts`); `codemirror.test.ts` runs the real one.

## Documents and search

`/ui/documents` lists the documents and, with words in `?q=`, searches them
(`/documents/search`, with snippets), with the same filters (`lib/filters.ts`: contact, type,
tags, contact and type groups, topics, space, lane, a field with a value; tags, groups and topics
as chips, several topics meaning one of them); there is no search page of its own. The field in the
header leads there; on narrow screens its icon opens the view with the field focused
(`#search`). `?sort=` holds the sort: a search by relevance, a list by the newest document date
unless chosen otherwise; the last choice of each, and grid or list (grid by default), stay in
this browser (`lib/listing.ts`). An admin's choice to include all users is a setting of the
user menu (`lib/scope.svelte.ts`), kept in this browser too and shown at the avatar; documents,
search, the inbox with its counter, rules and the counts at the master data follow it. Every
`localStorage` access is guarded: without storage the defaults hold.

## Header

The app's header holds the menu, the wordmark, the search, language, theme and the account menu
(`AppShell`). On a phone (below `sm`) language and theme are groups of the account menu instead
(`LanguageChoices`, `ThemeChoices`, which the header's own menus use too); the sign-in page keeps
both menus at every width.

## Master data

`/ui/admin/master-data` (admins) has four tabs in `?tab=`: contacts, document types, tags,
fields; an unknown one is the first. Each has a search in this browser (`lib/master-search.ts`:
contacts also by aliases and identifiers) and its button to add. The Tags tab holds every kind
of tag (`TagsPanel`, `lib/tag-kinds.ts`): a filter by kind in `?kind=`, one section per kind
with its tree, the collapsed sections remembered in this browser, and per tag its usage as a
link (documents; a contact group's contacts and a type group's types, which the contact and type
lists filter by `?group=` and `?topic=`). A new tag without a kind asks for it first.

On a phone (below `sm`, `lib/phone.svelte.ts`) a whole row opens the editor (`MasterRow`), and
the editors fill the screen with a bar on top (`EditorShell`); wider, the editors are dialogs
whose footer stays while the body scrolls. The contact editor puts the logo beside the name on a
phone, uploading and fetching below them (`ContactLogoEditor` with `beside`). Deleting is in the editors. Groups and topics are
picked in a sheet or dialog over the editor (`TagPicker`); there, in the document's tag input
and in its topic field, an admin's search that finds no tag of the kind named so offers to
create one at once or with details in a second dialog (`lib/tag-create.ts`,
`masterDataDialogs.createTag`).

Icons come from Lucide in the own build, each its own file loaded when it is shown. Chips and
type icons draw the 170 offered first from a small map (`lib/icons/curated-modules.ts`, which a
test keeps equal to `curated.ts`); the map of all 1,866 (about 43 KB gzip) loads only with the
icon picker's panel (`IconPickerPanel`, with the search and its words) or for an icon beyond
those (`lib/icons/load.ts`).

## Document view

`/ui/documents/{id}` serves the inbox and the document list (`/ui/inbox/{id}` leads there with
`?from=inbox`, which keeps the place among the inbox's documents) with the tabs details,
content, metadata and log (`?tab=`). From the desktop width on (`lg`) the view fills the window
and only its cards scroll; on narrow screens the page scrolls.

- **Details:** the archive PDF with PDF.js; until the archive exists (the text is still being
  recognised), the page images from the API stand in. Both are shown by `PageViewer`: the
  pages side by side, wrapping into rows, their width following the zoom (100 % is a page's
  printed size), so the zoom decides how many stand in a row. The layouts one page, two pages
  (the start; one on a narrow screen) and overview (four in a row, as far out as the zoom
  goes) are buttons, marked once zooming reaches them (`lib/viewer/zoom.ts`). Ctrl or ⌘ with
  the wheel, a pinch on a trackpad, Safari's gestures and two fingers zoom around the pointer
  (`lib/viewer/gestures.ts`); a double click enlarges a page and goes back; the keys + − 0.
  Pages are scaled while the zoom moves and drawn anew once it rests. A field with a quote
  marks it on the page while hovered or focused (the places come from `/review`, owner and
  admins), a click on its page jumps there; so does a code from the content tab.
- **Content:** the text as it is set, page by page, and beside it the facts and codes
  (`FactsPanel`, from `/facts`).
- **Metadata:** the files from `/files`: size, SHA-256, a PDF's information and XMP, an
  image's EXIF.

## API client

`openapi.json` is the API's document, checked in; a backend unit test fails while it differs from
the code. After an API change:

```sh
cd backend && uv run python -m papiq.composition.openapi ../web/openapi.json
cd web && pnpm gen:api   # writes src/lib/api/schema.ts
```

## Layout

| Path                           | Content                                                                         |
| ------------------------------ | ------------------------------------------------------------------------------- |
| `src/lib/api`                  | Client, `fetch` with session cookie and CSRF header, problem details            |
| `src/lib/session.svelte.ts`    | The signed-in user and the CSRF token, in memory                                |
| `src/lib/components`           | App shell, documents, dialogs, PDF viewer; `ui/` holds the shadcn-svelte ones   |
| `src/lib/components/document`  | The document view: head, fields, content (Markdown), log, prompt                |
| `src/lib/components/templates` | The template field, the template editor (sheet), the reference document picker  |
| `src/lib/templates`            | Liquid colours and completions, the CodeMirror editor, the values templates see |
| `src/lib/i18n.ts`              | Language switch, date, number and money formats                                 |
| `src/lib/periods.ts`           | Periods in ISO 8601 (`2026-Q2`, `2026-W17`), read also in German forms          |
| `src/lib/listing.ts`           | Sort and grid or list of the document view, remembered in this browser          |
| `src/lib/scope.svelte.ts`      | An admin's choice to include all users, remembered in this browser              |
| `src/routes`                   | `login`, the app pages in `(app)`; `(app)/dev/components` in development only   |
| `messages/{en,de}.json`        | All texts; a test fails on raw text in markup and on missing keys               |
| `tests/`                       | Tests across files (messages, raw text); `e2e/` the Playwright smoke test       |

Imports use `#lib/…` (package `imports`) with the file extension.
