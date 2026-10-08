import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { api } from '#lib/api/client.ts';
import { CSRF_HEADER, setSessionHooks } from '#lib/api/fetch.ts';
import { connectSession, session } from './session.svelte.ts';

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

function me(id: string, token: string): Response {
	return new Response(
		JSON.stringify({
			user: { id, username: id, role: 'user' },
			method: 'password',
			expires_at: '2026-10-09T12:00:00Z',
			csrf_token: token
		}),
		{ status: 200, headers: { 'content-type': 'application/json' } }
	);
}

const stale = () =>
	new Response(
		JSON.stringify({
			type: 'about:blank',
			title: 'Forbidden',
			status: 403,
			detail: `the header ${CSRF_HEADER} is missing or wrong`
		}),
		{ status: 403, headers: { 'content-type': 'application/problem+json' } }
	);

const events = { unauthorized: vi.fn(), replaced: vi.fn() };

beforeEach(() => {
	fetchMock.mockReset();
	events.unauthorized.mockReset();
	events.replaced.mockReset();
	vi.stubGlobal('fetch', fetchMock);
	connectSession(events);
});

afterEach(() => {
	session.clear();
	setSessionHooks(null);
	vi.unstubAllGlobals();
});

describe('a stale CSRF token', () => {
	it('is fetched anew for the same user, and the change is repeated', async () => {
		fetchMock.mockResolvedValueOnce(me('alice', 'old'));
		await session.load();
		fetchMock
			.mockResolvedValueOnce(stale())
			.mockResolvedValueOnce(me('alice', 'new'))
			.mockResolvedValueOnce(new Response('{}', { status: 201 }));

		const { response } = await api.POST('/api/v1/drawers', { body: { name: 'Bills' } });

		expect(response.status).toBe(201);
		expect(fetchMock.mock.calls[3][0].headers.get(CSRF_HEADER)).toBe('new');
		expect(events.replaced).not.toHaveBeenCalled();
	});

	it('never repeats a change as someone who signed in in another tab', async () => {
		fetchMock.mockResolvedValueOnce(me('alice', 'old'));
		await session.load();
		fetchMock.mockResolvedValueOnce(stale()).mockResolvedValueOnce(me('mallory', 'theirs'));

		const { response } = await api.POST('/api/v1/drawers', { body: { name: 'Bills' } });

		expect(response.status).toBe(403);
		expect(fetchMock).toHaveBeenCalledTimes(3); // sign-in check, the change, `me`
		expect(events.replaced).toHaveBeenCalledOnce();
	});
});
