import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { apiFetch, CSRF_HEADER, isChange, setSessionHooks, type SessionHooks } from './fetch.ts';

const ORIGIN = location.origin;
// The API's own wording (backend `rest/auth.py`).
const STALE = `the header ${CSRF_HEADER} is missing or wrong`;
const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

function problem(status: number, detail: string): Response {
	return new Response(JSON.stringify({ type: 'about:blank', title: 'Error', status, detail }), {
		status,
		headers: { 'content-type': 'application/problem+json' }
	});
}

function hooks(overrides: Partial<SessionHooks> = {}): SessionHooks {
	return {
		csrfToken: () => 'token-1',
		refreshCsrfToken: vi.fn(async () => 'token-2'),
		unauthorized: vi.fn(),
		...overrides
	};
}

beforeEach(() => {
	fetchMock.mockReset();
	vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
	setSessionHooks(null);
	vi.unstubAllGlobals();
});

describe('apiFetch', () => {
	it('sends the CSRF token on changes only', async () => {
		setSessionHooks(hooks());
		fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

		await apiFetch(new Request(`${ORIGIN}/api/v1/drawers`, { method: 'POST', body: '{}' }));
		await apiFetch(new Request(`${ORIGIN}/api/v1/drawers`));

		expect(fetchMock.mock.calls[0][0].headers.get(CSRF_HEADER)).toBe('token-1');
		expect(fetchMock.mock.calls[1][0].headers.get(CSRF_HEADER)).toBeNull();
	});

	it('keeps the token from other sites', async () => {
		setSessionHooks(hooks());
		fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
		await apiFetch(new Request('https://elsewhere.example/hook', { method: 'POST', body: '{}' }));
		expect(fetchMock.mock.calls[0][0].headers.get(CSRF_HEADER)).toBeNull();
	});

	it('sends cookies of this site', async () => {
		fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
		await apiFetch(new Request(`${ORIGIN}/api/v1/drawers`));
		expect(fetchMock.mock.calls[0][0].credentials).toBe('same-origin');
	});

	it('retries a change once with a fresh token when the token went stale', async () => {
		const session = hooks();
		setSessionHooks(session);
		fetchMock
			.mockResolvedValueOnce(problem(403, STALE))
			.mockResolvedValueOnce(new Response('{"id":1}', { status: 201 }));

		const response = await apiFetch(
			new Request(`${ORIGIN}/api/v1/drawers`, { method: 'POST', body: '{"name":"a"}' })
		);

		expect(response.status).toBe(201);
		expect(session.refreshCsrfToken).toHaveBeenCalledOnce();
		const retried = fetchMock.mock.calls[1][0];
		expect(retried.headers.get(CSRF_HEADER)).toBe('token-2');
		expect(await retried.text()).toBe('{"name":"a"}');
	});

	it('does not retry other refusals', async () => {
		const session = hooks();
		setSessionHooks(session);
		fetchMock.mockResolvedValue(problem(403, 'Only administrators may do this.'));

		const response = await apiFetch(
			new Request(`${ORIGIN}/api/v1/users`, { method: 'POST', body: '{}' })
		);

		expect(response.status).toBe(403);
		expect(fetchMock).toHaveBeenCalledOnce();
		expect(session.refreshCsrfToken).not.toHaveBeenCalled();
	});

	it('gives up when no fresh token comes', async () => {
		setSessionHooks(hooks({ refreshCsrfToken: async () => null }));
		fetchMock.mockResolvedValue(problem(403, STALE));

		const response = await apiFetch(new Request(`${ORIGIN}/api/v1/drawers`, { method: 'DELETE' }));

		expect(response.status).toBe(403);
		expect(fetchMock).toHaveBeenCalledOnce();
	});

	it('reports an expired session', async () => {
		const session = hooks();
		setSessionHooks(session);
		fetchMock.mockResolvedValue(problem(401, 'Not signed in.'));

		await apiFetch(new Request(`${ORIGIN}/api/v1/documents`));

		expect(session.unauthorized).toHaveBeenCalledOnce();
	});

	it.each(['/api/v1/auth/login', '/api/v1/auth/me', '/api/v1/auth/logout'])(
		'treats 401 from %s as part of signing in or out',
		async (path) => {
			const session = hooks();
			setSessionHooks(session);
			fetchMock.mockResolvedValue(problem(401, 'Wrong user name or password.'));

			await apiFetch(new Request(`${ORIGIN}${path}`, { method: 'POST', body: '{}' }));

			expect(session.unauthorized).not.toHaveBeenCalled();
		}
	);
});

describe('isChange', () => {
	it.each([
		['GET', false],
		['head', false],
		['OPTIONS', false],
		['POST', true],
		['PATCH', true],
		['PUT', true],
		['DELETE', true]
	])('%s → %s', (method, expected) => {
		expect(isChange(method)).toBe(expected);
	});
});
