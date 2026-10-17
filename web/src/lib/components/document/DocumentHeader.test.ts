import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { components } from '#lib/api/schema.ts';
import { m } from '#lib/paraglide/messages.js';
import type { GraphNode } from '#lib/pipeline.ts';
import { session } from '#lib/session.svelte.ts';
import type { Reading } from '#lib/vision.ts';
import DocumentHeader from './DocumentHeader.svelte';

type Document = components['schemas']['DocumentDetails'];

const OWNER = 'user-1';
const VISION = { model: 'claude-sonnet', endpoint: 'chat.example' };
const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

const graph: GraphNode[] = [
	{ node: 'receive', requires: [] },
	{ node: 'ocr', requires: ['receive'] },
	{ node: 'pages', requires: ['receive'] },
	{ node: 'parse', requires: ['ocr'] },
	{ node: 'file', requires: ['parse', 'pages'] }
];

function document(outcome: 'ok' | 'failed' = 'ok'): Document {
	return {
		id: 'doc-1',
		title: 'Scan',
		original_filename: 'scan.pdf',
		owner_id: OWNER,
		space_id: 'space-1',
		access: 'read_write',
		lane: outcome === 'failed' ? 'red' : 'yellow',
		page_count: 1,
		created_at: '2026-10-15T08:00:00Z',
		processing: {
			status: outcome === 'failed' ? 'failed' : 'review',
			run: 1,
			vision: false,
			nodes: graph.map(({ node }) => ({
				node,
				status: node === 'file' ? 'waiting' : 'done',
				outcome: node === 'file' ? null : node === 'parse' ? outcome : 'ok',
				started_at: null,
				finished_at: null
			}))
		}
	} as unknown as Document;
}

const NO_TEXT: Reading = {
	vision: false,
	pages: [{ page: 1, words: 0, unsure: 0, poor: false }],
	advice: 'no_text'
};

function props(overrides: Record<string, unknown> = {}) {
	return {
		document: document('failed'),
		lookup: null,
		graph,
		archive: true,
		inbox: null,
		vision: VISION,
		reading: NO_TEXT,
		onchanged: vi.fn(),
		...overrides
	};
}

beforeEach(() => {
	session.start({
		user: { id: OWNER, username: 'tobi', role: 'user' },
		method: 'password',
		expires_at: '2026-10-15T12:00:00Z',
		csrf_token: 'token'
	});
	fetchMock.mockReset();
	fetchMock.mockImplementation(async () => new Response('{}', { status: 202 }));
	vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
	session.clear();
	vi.unstubAllGlobals();
});

/** The menu of further actions; bits-ui opens it on a pointer press. */
async function openMenu() {
	await fireEvent.pointerDown(screen.getByRole('button', { name: m.doc_more() }), {
		button: 0,
		pointerType: 'mouse'
	});
	return userEvent.setup();
}

describe('DocumentHeader', () => {
	it('offers reading with the model, marked when advised', async () => {
		render(DocumentHeader, props());
		await openMenu();
		const item = await screen.findByRole('menuitem', { name: new RegExp(m.vision_read()) });
		expect(within(item).getByText(m.vision_recommended())).toBeTruthy();
	});

	it('asks before the pages go out, naming where, and then sends the request', async () => {
		const onchanged = vi.fn();
		render(DocumentHeader, props({ onchanged }));
		const user = await openMenu();
		await user.click(await screen.findByRole('menuitem', { name: new RegExp(m.vision_read()) }));
		const dialog = await screen.findByRole('alertdialog');
		expect(dialog.textContent).toContain(m.vision_advice_no_text());
		expect(dialog.textContent).toContain(
			m.vision_hint({ endpoint: 'chat.example', model: 'claude-sonnet' })
		);
		expect(fetchMock).not.toHaveBeenCalled();
		await user.click(within(dialog).getByRole('button', { name: m.vision_read() }));
		await waitFor(() => expect(onchanged).toHaveBeenCalled());
		const [request] = fetchMock.mock.calls[0];
		expect(request.method).toBe('POST');
		expect(new URL(request.url).pathname).toBe('/api/v1/documents/doc-1/vision');
	});

	it('offers nothing without a model', async () => {
		render(DocumentHeader, props({ vision: null }));
		await openMenu();
		await screen.findByRole('menuitem', { name: m.delete_title() });
		expect(screen.queryByRole('menuitem', { name: new RegExp(m.vision_read()) })).toBeNull();
	});

	it('is not marked without advice', async () => {
		render(DocumentHeader, props({ document: document(), reading: { ...NO_TEXT, advice: null } }));
		await openMenu();
		const item = await screen.findByRole('menuitem', { name: m.vision_read() });
		expect(within(item).queryByText(m.vision_recommended())).toBeNull();
	});

	describe('reprocessing from OCR a document the model read', () => {
		const read = { ...NO_TEXT, vision: true, advice: null };

		async function openReprocess(overrides: Record<string, unknown> = {}) {
			render(DocumentHeader, props({ document: document(), reading: read, ...overrides }));
			const user = await openMenu();
			await user.click(await screen.findByRole('menuitem', { name: m.reprocess_title() }));
			return { user, dialog: await screen.findByRole('dialog') };
		}

		it('reads with the model again when the person keeps that, naming where', async () => {
			const { user, dialog } = await openReprocess();
			const again = within(dialog).getByRole('checkbox', { name: m.reprocess_vision_again() });
			expect(again.getAttribute('aria-checked')).toBe('true');
			expect(dialog.textContent).toContain(
				m.vision_hint({ endpoint: 'chat.example', model: 'claude-sonnet' })
			);
			await user.click(within(dialog).getByRole('button', { name: m.reprocess_with_vision() }));
			await waitFor(() => expect(fetchMock).toHaveBeenCalled());
			const [request] = fetchMock.mock.calls[0];
			expect(new URL(request.url).pathname).toBe('/api/v1/documents/doc-1/vision');
		});

		it('reads with Tesseract when the person says no, and warns', async () => {
			const { user, dialog } = await openReprocess();
			await user.click(within(dialog).getByRole('checkbox', { name: m.reprocess_vision_again() }));
			expect(within(dialog).getByText(m.reprocess_vision_lost())).toBeTruthy();
			await user.click(within(dialog).getByRole('button', { name: m.reprocess_title() }));
			await waitFor(() => expect(fetchMock).toHaveBeenCalled());
			const [request] = fetchMock.mock.calls[0];
			expect(new URL(request.url).pathname).toBe('/api/v1/documents/doc-1/reprocess');
			expect(await request.json()).toEqual({ from_node: 'ocr' });
		});

		it('only warns without a model', async () => {
			const { dialog } = await openReprocess({ vision: null });
			expect(within(dialog).queryByRole('checkbox')).toBeNull();
			expect(within(dialog).getByText(m.reprocess_vision_lost())).toBeTruthy();
		});
	});
});
