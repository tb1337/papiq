# papiq web

Web UI: a single-page app on top of the Papiq REST API, served by the API under `/ui`. It talks to
the API only (`/api/v1`), through a TypeScript client generated from the API's OpenAPI document.

Stack: SvelteKit 3 (Svelte 5, static adapter, no server-side rendering), Tailwind CSS 4,
shadcn-svelte (bits-ui), Paraglide JS (English and German), openapi-fetch, Vitest. Fonts (Geist,
Geist Mono) are part of the build; no CDN.

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

For `pnpm dev`, start the API in `backend/` with `uv run python -m papiq.composition api` and
`PAPIQ_COOKIE_SECURE=false` (plain HTTP). Open http://localhost:5173/ui/.

## API client

`openapi.json` is the API's document, checked in; a backend unit test fails while it differs from
the code. After an API change:

```sh
cd backend && uv run python -m papiq.composition.openapi ../web/openapi.json
cd web && pnpm gen:api   # writes src/lib/api/schema.ts
```

## Layout

| Path | Content |
| --- | --- |
| `src/lib/api` | Client, `fetch` with session cookie and CSRF header, problem details |
| `src/lib/session.svelte.ts` | The signed-in user and the CSRF token, in memory |
| `src/lib/components` | App shell, lane badge, menus; `ui/` holds the shadcn-svelte components |
| `src/lib/i18n.ts` | Language switch, date, number and money formats |
| `src/routes` | `login`, the app pages in `(app)`; `(app)/dev/components` in development only |
| `messages/{en,de}.json` | All texts; a test fails on raw text in markup and on missing keys |
| `tests/` | Tests across files (messages, raw text) and the Playwright smoke test |

Imports use `#lib/…` (package `imports`) with the file extension.
