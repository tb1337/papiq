import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { createRawSnippet } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { LANGUAGE_NAMES, locales } from '#lib/i18n.ts';
import { m } from '#lib/paraglide/messages.js';
import { phone } from '#lib/phone.svelte.ts';
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

afterEach(() => {
	session.clear();
	phone.override(null);
});

function mainNavigation() {
	// The header holds the wide navigation; the narrow one lives in a closed sheet.
	return screen.getByRole('navigation', { name: m.nav_main() });
}

/** The account menu; bits-ui opens it on a pointer press. */
async function openAccountMenu(): Promise<HTMLElement> {
	await fireEvent.pointerDown(
		screen.getByRole('button', { name: m.account_menu({ username: 'tobi' }) }),
		{
			button: 0,
			pointerType: 'mouse'
		}
	);
	return screen.findByRole('menu');
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

	it('lists the inbox first and offers the search in the header', () => {
		signIn('admin');
		render(AppShell, { children });
		const links = within(mainNavigation()).getAllByRole('link');
		expect(links.map((link) => link.getAttribute('href'))).toEqual([
			'/ui/inbox',
			'/ui/documents',
			'/ui/spaces',
			'/ui/rules',
			'/ui/webhooks',
			'/ui/admin/master-data',
			'/ui/admin/users',
			'/ui/admin/classifier'
		]);
		expect(screen.getByRole('search')).toBeTruthy();
		expect(screen.getByRole('searchbox', { name: m.header_search_label() })).toBeTruthy();
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

	it('keeps language and theme in the header, beside the account menu', async () => {
		phone.override(false);
		signIn('user');
		render(AppShell, { children });
		expect(screen.getByRole('button', { name: m.theme_label() })).toBeTruthy();
		expect(screen.getByRole('button', { name: /^Sprache|^Language/ })).toBeTruthy();
		const menu = await openAccountMenu();
		expect(within(menu).queryByRole('menuitemradio')).toBeNull();
	});

	it('moves language and theme into the account menu on a phone', async () => {
		phone.override(true);
		signIn('user');
		render(AppShell, { children });
		expect(screen.queryByRole('button', { name: m.theme_label() })).toBeNull();
		expect(screen.queryByRole('button', { name: /^Sprache|^Language/ })).toBeNull();
		const menu = await openAccountMenu();
		const radios = within(menu).getAllByRole('menuitemradio');
		expect(radios.map((radio) => radio.textContent?.trim())).toEqual([
			...locales.map((locale) => LANGUAGE_NAMES[locale]),
			m.theme_light(),
			m.theme_dark(),
			m.theme_system()
		]);
		expect(within(menu).getByRole('menuitem', { name: m.sign_out() })).toBeTruthy();
	});
});
