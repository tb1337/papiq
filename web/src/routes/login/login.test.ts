import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { goto } from '$app/navigation';
import { m } from '#lib/paraglide/messages.js';
import { session } from '#lib/session.svelte.ts';
import Page from './+page.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

function json(status: number, body: unknown, type = 'application/json'): Response {
	return new Response(JSON.stringify(body), { status, headers: { 'content-type': type } });
}

const signedIn = {
	user: { id: '0199a6c4-0000-7000-8000-000000000001', username: 'tobi', role: 'admin' },
	method: 'password',
	expires_at: '2026-10-09T12:00:00Z',
	csrf_token: 'token'
};

// Fresh each time: a response body can be read once.
const secondFactor = () =>
	json(
		401,
		{ type: 'about:blank', title: 'Unauthorized', status: 401, second_factor_required: true },
		'application/problem+json'
	);
const refused = () =>
	json(
		401,
		{ type: 'about:blank', title: 'Unauthorized', status: 401, detail: 'Wrong.' },
		'application/problem+json'
	);

async function bodyOf(call: number): Promise<Record<string, unknown>> {
	return JSON.parse(await fetchMock.mock.calls[call][0].clone().text());
}

beforeEach(() => {
	fetchMock.mockReset();
	vi.mocked(goto).mockReset();
	vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
	session.clear();
	vi.unstubAllGlobals();
});

async function enterPassword() {
	const user = userEvent.setup();
	await user.type(screen.getByLabelText(m.login_username()), 'tobi');
	await user.type(screen.getByLabelText(m.login_password()), 'secret');
	await user.click(screen.getByRole('button', { name: m.login_submit() }));
	return user;
}

describe('sign-in page', () => {
	it('signs in and returns to the page the user came from', async () => {
		fetchMock.mockResolvedValueOnce(json(200, signedIn));
		render(Page, { data: { next: '/ui/inbox', oidc: null } });

		await enterPassword();

		expect(await bodyOf(0)).toMatchObject({ username: 'tobi', password: 'secret', code: null });
		expect(goto).toHaveBeenCalledWith('/ui/inbox', { replaceState: true });
		expect(session.user?.username).toBe('tobi');
		expect(session.csrfToken).toBe('token');
	});

	it('says when the password is wrong', async () => {
		fetchMock.mockResolvedValueOnce(refused());
		render(Page, { data: { next: '/ui/', oidc: null } });

		await enterPassword();

		expect((await screen.findByRole('alert')).textContent).toContain(m.login_failed());
		expect(goto).not.toHaveBeenCalled();
	});

	it('asks for the second factor, then signs in with the code', async () => {
		fetchMock.mockResolvedValueOnce(secondFactor()).mockResolvedValueOnce(json(200, signedIn));
		render(Page, { data: { next: '/ui/', oidc: null } });

		const user = await enterPassword();
		const code = await screen.findByLabelText(m.login_totp_label());
		await user.type(code, '123 456');
		await user.click(screen.getByRole('button', { name: m.login_submit() }));

		expect(await bodyOf(1)).toMatchObject({
			username: 'tobi',
			password: 'secret',
			code: '123456',
			recovery_code: null
		});
		expect(goto).toHaveBeenCalledWith('/ui/', { replaceState: true });
	});

	it('takes a recovery code instead', async () => {
		fetchMock.mockResolvedValueOnce(secondFactor()).mockResolvedValueOnce(refused());
		render(Page, { data: { next: '/ui/', oidc: null } });

		const user = await enterPassword();
		await user.click(await screen.findByRole('button', { name: m.login_use_recovery() }));
		await user.type(screen.getByLabelText(m.login_recovery_label()), ' abcd-efgh ');
		await user.click(screen.getByRole('button', { name: m.login_submit() }));

		expect(await bodyOf(1)).toMatchObject({ code: null, recovery_code: 'abcd-efgh' });
		expect((await screen.findByRole('alert')).textContent).toContain(m.login_code_failed());
	});

	it('shows how long to wait after too many attempts', async () => {
		fetchMock.mockResolvedValueOnce(
			new Response(
				JSON.stringify({ type: 'about:blank', title: 'Too Many Requests', status: 429 }),
				{
					status: 429,
					headers: { 'content-type': 'application/problem+json', 'retry-after': '30' }
				}
			)
		);
		render(Page, { data: { next: '/ui/', oidc: null } });

		await enterPassword();

		expect((await screen.findByRole('alert')).textContent).toContain(
			m.error_too_many({ seconds: 30 })
		);
	});

	it('offers the identity provider only when one is configured', () => {
		const { unmount } = render(Page, { data: { next: '/ui/', oidc: null } });
		expect(screen.queryByRole('link', { name: /Authentik/ })).toBeNull();
		unmount();

		render(Page, { data: { next: '/ui/rules', oidc: 'Authentik' } });
		const link = screen.getByRole('link', { name: m.login_oidc({ provider: 'Authentik' }) });
		expect(link.getAttribute('href')).toBe('/api/v1/auth/oidc/login?next=%2Fui%2Frules');
	});
});
