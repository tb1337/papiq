/**
 * The signed-in user, in memory only. The session cookie is HTTP-only and never visible here; the
 * CSRF token comes from `GET /auth/me` or the sign-in answer and is lost on reload, when
 * `load()` fetches it again.
 */
import { api } from '#lib/api/client.ts';
import { apiError, ApiError } from '#lib/api/problem.ts';
import { setSessionHooks } from '#lib/api/fetch.ts';
import type { components } from '#lib/api/schema.ts';

export type User = components['schemas']['UserOut'];
type SessionOut = components['schemas']['SessionOut'];

class Session {
	user = $state<User | null>(null);
	#csrf: string | null = null;

	get isAdmin(): boolean {
		return this.user?.role === 'admin';
	}

	get csrfToken(): string | null {
		return this.#csrf;
	}

	/**
	 * Who is signed in, from the server; false without a session. Every navigation calls this:
	 * the user object is replaced only when something about the user changed, so that effects
	 * reading `session.user` (the event stream, the uploads) do not run again for nothing.
	 */
	async load(): Promise<boolean> {
		const { data, error, response } = await api.GET('/api/v1/auth/me');
		if (data) {
			if (JSON.stringify(this.user) !== JSON.stringify(data.user)) this.user = data.user;
			this.#csrf = data.csrf_token;
			return true;
		}
		this.clear();
		if (response.status === 401) return false;
		throw await apiError(response, error);
	}

	/** Take over a new session (sign-in answer). */
	start(signedIn: SessionOut): void {
		this.user = signedIn.user;
		this.#csrf = signedIn.csrf_token;
	}

	clear(): void {
		this.user = null;
		this.#csrf = null;
	}

	/** Sign out on the server, then forget the session; also when the server says it has gone. */
	async logout(): Promise<void> {
		const { error, response } = await api.POST('/api/v1/auth/logout');
		this.clear();
		if (!response.ok && response.status !== 401) throw await apiError(response, error);
	}
}

export const session = new Session();

export interface SessionEvents {
	/** The session ended or never existed: sign in again. */
	unauthorized(): void;
	/** Another sign-in in this browser (another tab) replaced the session: start afresh. */
	replaced(): void;
}

/** Connect the API client to the session. */
export function connectSession({ unauthorized, replaced }: SessionEvents): void {
	setSessionHooks({
		csrfToken: () => session.csrfToken,
		refreshCsrfToken: async () => {
			const before = session.user?.id ?? null;
			try {
				if (!(await session.load())) return null;
			} catch (error) {
				if (error instanceof ApiError) return null;
				throw error;
			}
			// The cookie is shared by all tabs: never repeat a change as someone else.
			if (before === null || session.user?.id !== before) {
				if (before !== null) replaced();
				return null;
			}
			return session.csrfToken;
		},
		unauthorized: () => {
			session.clear();
			unauthorized();
		}
	});
}
