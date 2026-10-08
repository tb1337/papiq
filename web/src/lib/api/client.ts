import createClient from 'openapi-fetch';
import { apiFetch } from './fetch.ts';
import type { paths } from './schema.ts';

/**
 * The typed client of the Papiq API (`/api/v1`); paths as in `openapi.json`. The API is on the
 * page's own origin; spelled out because `Request` outside a browser takes absolute URLs only.
 */
export const api = createClient<paths>({
	baseUrl: globalThis.location?.origin ?? '',
	fetch: apiFetch
});
