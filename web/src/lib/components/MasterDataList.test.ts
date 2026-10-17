import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { phone } from '#lib/phone.svelte.ts';
import MasterDataList from './MasterDataList.svelte';

const nav = vi.hoisted(() => ({
	url: new URL('http://papiq/ui/admin/master-data?tab=contacts'),
	goto: vi.fn()
}));
vi.mock('$app/state', () => ({
	page: {
		get url() {
			return nav.url;
		}
	}
}));
vi.mock('$app/navigation', () => ({ goto: nav.goto }));

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

const tag = (id: string, path: string[], parent: string | null, scope: string) => ({
	id,
	name: path[path.length - 1],
	path,
	parent_id: parent,
	scope,
	assignment: 'manual',
	shown: false,
	icon: null,
	color: null,
	created_at: '2026-10-17T12:00:00Z'
});

const contact = (id: string, name: string, extra: object = {}) => ({
	id,
	name,
	aliases: [],
	identifiers: [],
	group_ids: [],
	topic_ids: [],
	logo: null,
	logo_failure: null,
	document_count: 0,
	created_at: '2026-10-17T12:00:00Z',
	...extra
});

const CONTACTS = [
	contact('c1', 'Nord Versicherungsgruppe', {
		aliases: ['Nord Krankenversicherung AG', 'Nord Leben AG'],
		group_ids: ['g2'],
		topic_ids: ['p1', 'p2', 'p3'],
		document_count: 104
	}),
	contact('c2', 'Sparkasse Musterstadt', {
		identifiers: [
			{ kind: 'iban', value: 'DE02120300000000202051', source: 'manual', topic_id: null }
		],
		group_ids: ['g3']
	}),
	contact('c3', 'Stadtwerke', { aliases: ['SWM'] })
];

function json(body: unknown) {
	return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
}

beforeEach(() => {
	nav.url = new URL('http://papiq/ui/admin/master-data?tab=contacts');
	nav.goto.mockReset();
	fetchMock.mockReset();
	fetchMock.mockImplementation(async (request) => {
		const path = new URL(request.url).pathname;
		if (path === '/api/v1/contacts') return json(CONTACTS);
		if (path === '/api/v1/tags') {
			return json([
				tag('g1', ['Insurer'], null, 'contact'),
				tag('g2', ['Insurer', 'Health insurer'], 'g1', 'contact'),
				tag('g3', ['Bank'], null, 'contact')
			]);
		}
		return json([]);
	});
	vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
	vi.unstubAllGlobals();
	phone.override(null);
	masterDataDialogs.close();
});

describe('MasterDataList', () => {
	it('tells under a contact’s name its groups, aliases, identifiers and topics', async () => {
		render(MasterDataList, { kind: 'contacts' });
		const nord = (await screen.findByText('Nord Versicherungsgruppe')).closest('li')!;
		expect(within(nord).getByText('Health insurer')).toBeTruthy();
		expect(
			within(nord).getByText(
				m.master_alias_more({ alias: 'Nord Krankenversicherung AG', count: 1 })
			)
		).toBeTruthy();
		expect(within(nord).getByText(m.master_topics_many({ count: 3 }))).toBeTruthy();
		const sparkasse = screen.getByText('Sparkasse Musterstadt').closest('li')!;
		expect(within(sparkasse).getByText(m.master_identifier_one())).toBeTruthy();
		expect(
			within(nord).getByRole('link', { name: m.master_documents({ count: '104' }) })
		).toBeTruthy();
	});

	it('finds contacts by alias and by an identifier written with spaces', async () => {
		const { rerender } = render(MasterDataList, { kind: 'contacts', query: 'swm' });
		expect(await screen.findByText('Stadtwerke')).toBeTruthy();
		expect(screen.queryByText('Sparkasse Musterstadt')).toBeNull();
		await rerender({ query: 'DE02 1203 0000' });
		expect(screen.getByText('Sparkasse Musterstadt')).toBeTruthy();
		expect(screen.queryByText('Stadtwerke')).toBeNull();
	});

	it('lists the contacts of a group or one below it, as a link from the Tags tab asks', async () => {
		nav.url = new URL('http://papiq/ui/admin/master-data?tab=contacts&group=g1');
		render(MasterDataList, { kind: 'contacts' });
		expect(await screen.findByText('Nord Versicherungsgruppe')).toBeTruthy();
		expect(screen.queryByText('Sparkasse Musterstadt')).toBeNull();
		const label = m.master_filter_group({ name: 'Insurer' });
		await fireEvent.click(
			screen.getByRole('button', { name: m.master_filter_remove({ name: label }) })
		);
		expect(nav.goto).toHaveBeenCalledWith('?tab=contacts', { reset: false });
	});

	it('ignores a filter that names no tag of its kind, and takes it out of the URL', async () => {
		nav.url = new URL('http://papiq/ui/admin/master-data?tab=contacts&group=gone');
		render(MasterDataList, { kind: 'contacts' });
		expect(await screen.findByText('Nord Versicherungsgruppe')).toBeTruthy();
		expect(screen.getByText('Sparkasse Musterstadt')).toBeTruthy();
		expect(screen.queryByRole('button', { name: /gone/ })).toBeNull();
		await waitFor(() =>
			expect(nav.goto).toHaveBeenCalledWith('?tab=contacts', { replace: true, reset: false })
		);
	});

	it('tells a type’s description under its name, and nothing for a type without one', async () => {
		fetchMock.mockImplementation(async (request) => {
			const path = new URL(request.url).pathname;
			if (path === '/api/v1/document-types') {
				return json([
					{ id: 't1', name: 'Rechnung', description: 'Eine Forderung', group_ids: [], icon: null },
					{ id: 't2', name: 'Kündigung', description: null, group_ids: [], icon: null }
				]);
			}
			return json([]);
		});
		render(MasterDataList, { kind: 'document-types' });
		const invoice = (await screen.findByText('Rechnung')).closest('li')!;
		expect(within(invoice).getByText('Eine Forderung')).toBeTruthy();
		const notice = screen.getByText('Kündigung').closest('li')!;
		expect(notice.textContent?.replace(/\s+/g, ' ').trim()).toMatch(/^Kündigung\b/);
		expect(within(notice).queryByText(/Beschreibung|description/i)).toBeNull();
	});

	it('on a phone opens a contact with a tap on its row', async () => {
		phone.override(true);
		render(MasterDataList, { kind: 'contacts' });
		const name = await screen.findByText('Nord Versicherungsgruppe');
		expect(
			screen.queryByRole('button', { name: m.master_edit({ name: 'Nord Versicherungsgruppe' }) })
		).toBeNull();
		expect(screen.queryByRole('link')).toBeNull();
		await fireEvent.click(name.closest('button')!);
		expect(masterDataDialogs.request).toMatchObject({ kind: 'contacts', item: { id: 'c1' } });
	});
});
