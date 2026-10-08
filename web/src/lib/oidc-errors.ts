import { m } from '#lib/paraglide/messages.js';

/**
 * The text for the `error` code the API puts in the address when the sign-in or link at the
 * identity provider fails (`/auth/oidc/callback`); null for no or an unknown code.
 */
export function oidcErrorMessage(code: string | null | undefined): string | null {
	switch (code) {
		case 'denied':
			return m.oidc_error_denied();
		case 'failed':
			return m.oidc_error_failed();
		case 'conflict':
			return m.oidc_error_conflict();
		case 'provider':
			return m.oidc_error_provider();
		default:
			return null;
	}
}
