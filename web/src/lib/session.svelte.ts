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

	/** Who is signed in, from the server; false without a session. */
	async load(): Promise<boolean> {
		const { data, response } = await api.GET('/api/v1/auth/me');
		if (data) {
			this.user = data.user;
			this.#csrf = data.csrf_token;
			return true;
		}
		this.clear();
		if (response.status === 401) return false;
		throw await apiError(response);
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
		const { response } = await api.POST('/api/v1/auth/logout');
		this.clear();
		if (!response.ok && response.status !== 401) throw await apiError(response);
	}
}

export const session = new Session();

/** Connect the API client to the session; `unauthorized` leads to the sign-in page. */
export function connectSession(unauthorized: () => void): void {
	setSessionHooks({
		csrfToken: () => session.csrfToken,
		refreshCsrfToken: async () => {
			try {
				return (await session.load()) ? session.csrfToken : null;
			} catch (error) {
				if (error instanceof ApiError) return null;
				throw error;
			}
		},
		unauthorized: () => {
			session.clear();
			unauthorized();
		}
	});
}
