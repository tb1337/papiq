import { BASE as base } from '#lib/base.ts';

const MAX_LENGTH = 2000;
// Control characters and spaces, as the server's `safe_redirect` refuses them.
// eslint-disable-next-line no-control-regex
const CONTROL = /[\u0000-\u0020\u007f]/;

/** The app's start page. */
export function home(): string {
	return `${base}/`;
}

/**
 * `target` if it is a page of this app (a path below the base, `/ui/…`), else the start page.
 * Guards the return after signing in against leaving the app or the site.
 */
export function safeNext(target: string | null | undefined): string {
	const path = target?.split(/[?#]/, 1)[0] ?? '';
	if (
		!target ||
		target.length > MAX_LENGTH ||
		!(target === base || target.startsWith(`${base}/`)) ||
		target.startsWith('//') ||
		target.includes('\\') ||
		CONTROL.test(target) ||
		// No dot segments, also encoded: the browser would resolve them out of the app.
		/(^|\/)\.\.?(\/|$)/.test(path) ||
		/%2e|%2f|%5c/i.test(path)
	) {
		return home();
	}
	return target;
}

/** The sign-in page, returning to `next` afterwards. */
export function loginHref(next: string): string {
	const target = safeNext(next);
	return target === home() ? `${base}/login` : `${base}/login?next=${encodeURIComponent(target)}`;
}

/** The start of signing in through the identity provider (a page of the API, not of the app). */
export function oidcLoginHref(next: string): string {
	return `/api/v1/auth/oidc/login?next=${encodeURIComponent(safeNext(next))}`;
}
