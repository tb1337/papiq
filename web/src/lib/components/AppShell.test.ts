import { render, screen, within } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { m } from '#lib/paraglide/messages.js';
import { session, type User } from '#lib/session.svelte.ts';
import AppShell from './AppShell.svelte';

vi.mock('$app/navigation', () => ({ goto: vi.fn() }));
vi.mock('$app/state', () => ({ page: { url: new URL('http://papiq/ui/inbox') } }));

const children = createRawSnippet(() => ({ render: () => '<p>content</p>' }));

function signIn(role: User['role']) {
	session.start({
		user: { id: '0199a6c4-0000-7000-8000-000000000001', username: 'tobi', role },
		method: 'password',
		expires_at: '2026-10-09T12:00:00Z',
		csrf_token: 'token'
	});
}

afterEach(() => session.clear());

function mainNavigation() {
	// The header holds the wide navigation; the narrow one lives in a closed sheet.
	return screen.getByRole('navigation', { name: m.nav_main() });
}

describe('AppShell', () => {
	it('shows users their areas without the admin areas', () => {
		signIn('user');
		render(AppShell, { children });
		const nav = mainNavigation();
		expect(within(nav).getByRole('link', { name: m.nav_documents() })).toBeTruthy();
		expect(within(nav).queryByRole('link', { name: m.nav_users() })).toBeNull();
		expect(within(nav).queryByRole('link', { name: m.nav_master_data() })).toBeNull();
	});

	it('shows admins the admin areas', () => {
		signIn('admin');
		render(AppShell, { children });
		const nav = mainNavigation();
		expect(within(nav).getByRole('link', { name: m.nav_users() }).getAttribute('href')).toBe(
			'/ui/admin/users'
		);
		expect(within(nav).getByRole('link', { name: m.nav_master_data() })).toBeTruthy();
	});

	it('marks the current area', () => {
		signIn('user');
		render(AppShell, { children });
		const inbox = within(mainNavigation()).getByRole('link', { name: m.nav_inbox() });
		expect(inbox.getAttribute('aria-current')).toBe('page');
	});

	it('offers a skip link and the account menu', () => {
		signIn('user');
		render(AppShell, { children });
		expect(screen.getByRole('link', { name: m.skip_to_content() }).getAttribute('href')).toBe(
			'#main'
		);
		expect(screen.getByRole('button', { name: m.account_menu({ username: 'tobi' }) })).toBeTruthy();
		expect(screen.getByText('content')).toBeTruthy();
	});
});
