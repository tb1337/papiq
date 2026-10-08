/**
 * The `fetch` of the API client: cookies of this site, the CSRF header on every change, one
 * retry when the CSRF token went stale, and a hook for an expired sign-in.
 */

export const CSRF_HEADER = 'X-CSRF-Token';
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS']);
// Answers 401 here are part of signing in or out, not an expired session to report.
const SIGN_IN_PATHS = ['/api/v1/auth/login', '/api/v1/auth/me', '/api/v1/auth/logout'];

export interface SessionHooks {
	/** The CSRF token of the current session, if any. */
	csrfToken(): string | null;
	/**
	 * Fetch the session's CSRF token anew (after a 403 for a stale one); null if there is none or
	 * the session now belongs to someone else (the change is then not repeated).
	 */
	refreshCsrfToken(): Promise<string | null>;
	/** The session ended or never existed: sign in again. */
	unauthorized(): void;
}

let hooks: SessionHooks | null = null;

export function setSessionHooks(value: SessionHooks | null): void {
	hooks = value;
}

export function isChange(method: string): boolean {
	return !SAFE_METHODS.has(method.toUpperCase());
}

function isSignIn(url: string): boolean {
	const path = new URL(url, 'http://papiq').pathname;
	return SIGN_IN_PATHS.includes(path);
}

async function isStaleCsrf(response: Response): Promise<boolean> {
	if (response.status !== 403) return false;
	try {
		const body: unknown = await response.clone().json();
		const detail = body && typeof body === 'object' && 'detail' in body ? body.detail : null;
		return typeof detail === 'string' && detail.includes(CSRF_HEADER);
	} catch {
		return false;
	}
}

function sameOrigin(url: string): boolean {
	return typeof location === 'undefined' || new URL(url).origin === location.origin;
}

function withCsrf(request: Request, token: string | null): Request {
	// The token belongs to this site only.
	if (!token || !isChange(request.method) || !sameOrigin(request.url)) return request;
	const headers = new Headers(request.headers);
	headers.set(CSRF_HEADER, token);
	return new Request(request, { headers });
}

export async function apiFetch(input: Request): Promise<Response> {
	const request = new Request(input, { credentials: 'same-origin' });
	// A copy for the retry: a request body can be read once.
	const retry = isChange(request.method) ? request.clone() : null;
	let response = await fetch(withCsrf(request, hooks?.csrfToken() ?? null));
	if (retry && hooks && (await isStaleCsrf(response))) {
		const token = await hooks.refreshCsrfToken();
		if (token) response = await fetch(withCsrf(retry, token));
	}
	if (response.status === 401 && !isSignIn(request.url)) hooks?.unauthorized();
	return response;
}
