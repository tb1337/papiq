import { fireEvent, render, screen, within } from '@testing-library/svelte';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { phone } from '#lib/phone.svelte.ts';
import TagsPanel from './TagsPanel.svelte';

const nav = vi.hoisted(() => ({
	url: new URL('http://papiq/ui/admin/master-data?tab=tags'),
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

function tag(id: string, path: string[], parent: string | null, scope: string, extra: object = {}) {
	return {
		id,
		name: path[path.length - 1],
		path,
		parent_id: parent,
		scope,
		assignment: scope === 'document' || scope === 'topic' ? 'auto' : 'manual',
		shown: scope === 'document' || scope === 'topic',
		icon: null,
		color: null,
		document_count: 0,
		contact_count: scope === 'contact' || scope === 'topic' ? 0 : null,
		document_type_count: scope === 'document_type' ? 0 : null,
		created_at: '2026-10-17T12:00:00Z',
		...extra
	};
}

const TAGS = [
	tag('t1', ['Insurance'], null, 'document', { document_count: 3 }),
	tag('t2', ['Insurance', 'Health'], 't1', 'document', { document_count: 2 }),
	tag('t3', ['Paid'], null, 'document', { assignment: 'manual', shown: false }),
	tag('g1', ['Insurer'], null, 'contact', { contact_count: 2, document_count: 5, shown: true }),
	tag('y1', ['Bills'], null, 'document_type', { document_type_count: 1 }),
	tag('p1', ['Work'], null, 'topic', { contact_count: 1, document_count: 4 })
];

function json(body: unknown) {
	return new Response(JSON.stringify(body), { headers: { 'content-type': 'application/json' } });
}

beforeEach(() => {
	nav.url = new URL('http://papiq/ui/admin/master-data?tab=tags');
	nav.goto.mockReset();
	localStorage.clear();
	fetchMock.mockReset();
	fetchMock.mockImplementation(async (request) => {
		const url = new URL(request.url);
		if (url.pathname === '/api/v1/tags') {
			expect(url.searchParams.get('document_counts')).toBe('true');
			return json(TAGS);
		}
		if (url.pathname === '/api/v1/document-types') {
			return json([
				{ id: 'd1', name: 'Invoice', group_ids: ['y1'], created_at: '2026-10-17T12:00:00Z' },
				{ id: 'd2', name: 'Letter', group_ids: [], created_at: '2026-10-17T12:00:00Z' }
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

const section = (title: string) => screen.getByRole('heading', { name: title }).closest('section')!;

describe('TagsPanel', () => {
	it('shows a section per kind with its tree, how tags are set and their usage as links', async () => {
		render(TagsPanel);
		expect(await screen.findByText('Health')).toBeTruthy();
		expect(screen.getAllByRole('heading').map((heading) => heading.textContent?.trim())).toEqual([
			m.master_document_tags(),
			m.master_contact_groups(),
			m.master_type_groups(),
			m.master_topics()
		]);
		// A child one level further in than its parent; no counts in the headers.
		const depth = (name: string) =>
			screen
				.getByText(name)
				.closest('li')!
				.querySelector('[data-depth]')!
				.getAttribute('data-depth');
		expect([depth('Insurance'), depth('Health'), depth('Paid')]).toEqual(['0', '1', '0']);
		expect(screen.getByText(m.tag_below({ count: 1 }))).toBeTruthy();
		const documents = within(section(m.master_document_tags()));
		// What differs from the default of the kind: set by hand, hidden; a group shown.
		expect(documents.getByText(m.tag_assignment_manual())).toBeTruthy();
		expect(documents.getByText(m.tag_hidden())).toBeTruthy();
		expect(within(section(m.master_type_groups())).queryByText(m.tag_hidden())).toBeNull();
		const href = (scope: HTMLElement, name: string) =>
			within(scope).getByRole('link', { name }).getAttribute('href');
		expect(href(section(m.master_document_tags()), m.master_documents({ count: '3' }))).toBe(
			'/ui/documents?tag=t1'
		);
		const groups = section(m.master_contact_groups());
		expect(within(groups).getByText(m.tag_shown_badge())).toBeTruthy();
		expect(href(groups, m.master_contacts_many({ count: '2' }))).toBe(
			'/ui/admin/master-data?tab=contacts&group=g1'
		);
		// A group's documents are not its usage: its contacts are.
		expect(within(groups).queryByText(m.master_documents({ count: '5' }))).toBeNull();
		const types = section(m.master_type_groups());
		expect(href(types, m.master_types_one())).toBe(
			'/ui/admin/master-data?tab=document-types&group=y1'
		);
		expect(within(types).getByText('Invoice')).toBeTruthy();
		const topics = section(m.master_topics());
		expect(href(topics, m.master_contacts_one())).toBe(
			'/ui/admin/master-data?tab=contacts&topic=p1'
		);
		expect(href(topics, m.master_documents({ count: '4' }))).toBe('/ui/documents?topic=p1');
	});

	it('shows the kind the URL names and puts another choice there', async () => {
		nav.url = new URL('http://papiq/ui/admin/master-data?tab=tags&kind=topics');
		render(TagsPanel);
		expect(await screen.findByText('Work')).toBeTruthy();
		expect(screen.getAllByRole('heading').map((heading) => heading.textContent?.trim())).toEqual([
			m.master_topics()
		]);
		const kinds = screen.getByRole('group', { name: m.tag_kinds_label() });
		const pressed = within(kinds).getByRole('button', { pressed: true });
		expect(pressed.textContent?.trim()).toBe(m.master_topics());
		await fireEvent.click(within(kinds).getByRole('button', { name: m.master_contact_groups() }));
		expect(nav.goto).toHaveBeenLastCalledWith('?tab=tags&kind=contact-groups', { reset: false });
		await fireEvent.click(within(kinds).getByRole('button', { name: m.tag_kinds_all() }));
		expect(nav.goto).toHaveBeenLastCalledWith('?tab=tags', { reset: false });
	});

	it('keeps a collapsed section collapsed in this browser', async () => {
		const first = render(TagsPanel);
		expect(await screen.findByText('Insurer')).toBeTruthy();
		await fireEvent.click(
			screen.getByRole('button', { name: m.tree_collapse({ name: m.master_contact_groups() }) })
		);
		expect(screen.queryByText('Insurer')).toBeNull();
		expect(JSON.parse(localStorage.getItem('papiq.master-data.tags.collapsed')!)).toEqual([
			'contact-groups'
		]);
		first.unmount();
		render(TagsPanel);
		expect(await screen.findByText('Health')).toBeTruthy();
		expect(screen.queryByText('Insurer')).toBeNull();
		await fireEvent.click(
			screen.getByRole('button', { name: m.tree_expand({ name: m.master_contact_groups() }) })
		);
		expect(screen.getByText('Insurer')).toBeTruthy();
		expect(localStorage.getItem('papiq.master-data.tags.collapsed')).toBeNull();
	});

	it('shows a kind the URL names open, though collapsed in this browser', async () => {
		localStorage.setItem('papiq.master-data.tags.collapsed', '["topics"]');
		nav.url = new URL('http://papiq/ui/admin/master-data?tab=tags&kind=topics');
		render(TagsPanel);
		expect(await screen.findByText('Work')).toBeTruthy();
		// Nothing to collapse while it is the only one.
		expect(
			screen.queryByRole('button', { name: m.tree_expand({ name: m.master_topics() }) })
		).toBeNull();
		expect(localStorage.getItem('papiq.master-data.tags.collapsed')).toBe('["topics"]');
	});

	it('points to the list of a section only while it is open', async () => {
		render(TagsPanel);
		const collapse = await screen.findByRole('button', {
			name: m.tree_collapse({ name: m.master_contact_groups() })
		});
		const list = collapse.getAttribute('aria-controls');
		expect(list && document.getElementById(list)).toBeTruthy();
		await fireEvent.click(collapse);
		const expand = screen.getByRole('button', {
			name: m.tree_expand({ name: m.master_contact_groups() })
		});
		expect(expand.hasAttribute('aria-controls')).toBe(false);
	});

	it('collapses a tag with tags below it', async () => {
		render(TagsPanel);
		const collapse = await screen.findByRole('button', {
			name: m.tree_collapse({ name: 'Insurance' })
		});
		expect(collapse.getAttribute('aria-expanded')).toBe('true');
		await fireEvent.click(collapse);
		expect(screen.queryByText('Health')).toBeNull();
		expect(screen.getByText('Paid')).toBeTruthy();
		await fireEvent.click(
			screen.getByRole('button', { name: m.tree_expand({ name: 'Insurance' }) })
		);
		expect(screen.getByText('Health')).toBeTruthy();
	});

	it('finds tags by the name of a tag above them and shows only the sections with a match', async () => {
		render(TagsPanel, { query: 'insurance health' });
		expect(await screen.findByText('Health')).toBeTruthy();
		expect(screen.getByText('Insurance')).toBeTruthy(); // above the match
		expect(screen.queryByText('Paid')).toBeNull();
		expect(screen.getAllByRole('heading').map((heading) => heading.textContent?.trim())).toEqual([
			m.master_document_tags()
		]);
	});

	it('adds a tag of a section’s kind and opens a tag to edit', async () => {
		render(TagsPanel);
		await screen.findByText('Insurer');
		await fireEvent.click(screen.getByRole('button', { name: m.master_add_contact_group() }));
		expect(masterDataDialogs.request?.kind).toBe('contact-groups');
		await fireEvent.click(screen.getByRole('button', { name: m.master_edit({ name: 'Work' }) }));
		expect(masterDataDialogs.request).toMatchObject({ kind: 'topics', item: { id: 'p1' } });
	});

	it('on a phone opens a tag with a tap on its row, short hints, the usage a number', async () => {
		phone.override(true);
		render(TagsPanel);
		const paid = await screen.findByText('Paid');
		expect(screen.queryByRole('button', { name: m.master_edit({ name: 'Paid' }) })).toBeNull();
		expect(screen.queryByRole('link')).toBeNull();
		expect(within(paid.closest('button')!).getByText(m.tag_manual_short())).toBeTruthy();
		const insurer = screen.getByText('Insurer').closest('button')!;
		expect(within(insurer).getByText('2')).toBeTruthy();
		expect(within(insurer).getByText(m.master_contacts_many({ count: '2' }))).toBeTruthy();
		await fireEvent.click(insurer);
		expect(masterDataDialogs.request).toMatchObject({ kind: 'contact-groups', item: { id: 'g1' } });
		// The section's description stays on wider screens.
		expect(screen.queryByText(m.tag_kind_contact_about())).toBeNull();
	});
});
