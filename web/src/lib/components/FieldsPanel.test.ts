import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { phone } from '#lib/phone.svelte.ts';
import { session } from '#lib/session.svelte.ts';
import WithDialogs from '#lib/testing/WithDialogs.svelte';
import FieldsPanel from './FieldsPanel.svelte';

const INVOICE = '0199a6c4-0000-7000-8000-0000000000e1';
const REMINDER = '0199a6c4-0000-7000-8000-0000000000e2';

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

function json(body: unknown, status = 200) {
	return new Response(JSON.stringify(body), {
		status,
		headers: { 'content-type': 'application/json' }
	});
}

beforeEach(() => {
	session.start({
		user: { id: 'admin', username: 'admin', role: 'admin' },
		method: 'password',
		expires_at: '2026-10-14T12:00:00Z',
		csrf_token: 'token'
	});
	fetchMock.mockReset();
	fetchMock.mockImplementation(async (request) => {
		const path = new URL(request.url).pathname;
		if (request.method === 'GET' && path === '/api/v1/fields') {
			return json([
				{
					id: 'f1',
					name: 'Rechnungsnummer',
					key: 'rechnungsnummer',
					data_type: 'text',
					document_type_ids: [INVOICE, REMINDER],
					choices: [],
					required: false,
					required_document_type_ids: [INVOICE],
					description: 'Die Nummer, unter der die Rechnung geführt wird',
					created_at: '2026-10-13T12:00:00Z'
				}
			]);
		}
		if (request.method === 'GET' && path === '/api/v1/document-types') {
			return json([
				{ id: INVOICE, name: 'Rechnung', created_at: '2026-10-13T12:00:00Z' },
				{ id: REMINDER, name: 'Mahnung', created_at: '2026-10-13T12:00:00Z' }
			]);
		}
		return json({}, 201);
	});
	vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
	masterDataDialogs.close();
	phone.override(null);
	session.clear();
	vi.unstubAllGlobals();
	// A dialog makes the page unclickable until it has closed; the next test must not wait.
	window.document.body.style.pointerEvents = '';
});

describe('FieldsPanel', () => {
	it('lists where a field is required', async () => {
		render(FieldsPanel);
		const required = m.field_def_required_in({ scope: 'Rechnung' });
		expect(await screen.findByText((text) => text.includes(required))).toBeTruthy();
	});

	it('makes a field required for one of its types', async () => {
		render(WithDialogs, { component: FieldsPanel });
		await screen.findByText('Rechnungsnummer');
		masterDataDialogs.open('fields');
		await userEvent.type(await screen.findByLabelText(m.field_name()), 'Betrag');
		await userEvent.click(screen.getByLabelText(m.field_def_scope_global()));
		expect(screen.getByText(m.field_def_no_type())).toBeTruthy();
		// The types come from a search, each then required or not.
		await userEvent.click(screen.getByRole('button', { name: m.field_def_add_type() }));
		// The popover is placed by measuring, which jsdom does not: its options are found by text.
		await userEvent.click(
			(await screen.findByText('Mahnung', { selector: '[role="option"] span' })).closest(
				'[role="option"]'
			)!
		);
		await userEvent.click(
			screen.getByRole('switch', { name: m.assignment_required_for({ name: 'Mahnung' }) })
		);
		expect(screen.queryByRole('switch', { name: /Rechnung/ })).toBeNull();
		await userEvent.click(screen.getByRole('button', { name: m.save() }));
		const post = fetchMock.mock.calls.map((call) => call[0]).find((call) => call.method === 'POST');
		expect(await post?.json()).toEqual({
			name: 'Betrag',
			data_type: 'text',
			description: null,
			assignment: 'auto',
			document_type_ids: [REMINDER],
			required: false,
			required_document_type_ids: [REMINDER]
		});
	});

	it('makes a field one that is set by hand only', async () => {
		render(WithDialogs, { component: FieldsPanel });
		masterDataDialogs.open('fields');
		await userEvent.type(await screen.findByLabelText(m.field_name()), 'ASN');
		await userEvent.click(screen.getByLabelText(m.field_def_manual()));
		await userEvent.click(screen.getByRole('button', { name: m.save() }));
		const post = fetchMock.mock.calls.map((call) => call[0]).find((call) => call.method === 'POST');
		expect(await post?.json()).toMatchObject({ name: 'ASN', assignment: 'manual' });
	});

	it('shows what belongs in a field and lets an admin change it', async () => {
		render(WithDialogs, { component: FieldsPanel });
		const hint = 'Die Nummer, unter der die Rechnung geführt wird';
		expect(await screen.findByText(hint)).toBeTruthy();
		await userEvent.click(
			screen.getByRole('button', { name: m.master_edit({ name: 'Rechnungsnummer' }) })
		);
		// The key templates use stands in the editor.
		expect(within(screen.getByRole('dialog')).getByText('field.rechnungsnummer')).toBeTruthy();
		const description = screen.getByLabelText(m.field_def_description()) as HTMLTextAreaElement;
		expect(description.value).toBe(hint);
		await userEvent.clear(description);
		await userEvent.type(description, 'Rechnungsnummer, nicht die Kundennummer');
		await userEvent.click(screen.getByRole('button', { name: m.save() }));
		const patch = fetchMock.mock.calls
			.map((call) => call[0])
			.find((call) => call.method === 'PATCH');
		expect(await patch?.json()).toMatchObject({
			description: 'Rechnungsnummer, nicht die Kundennummer'
		});
	});

	it('finds a field by its name, key or description', async () => {
		const { rerender } = render(FieldsPanel, { query: 'kundennummer' });
		expect(await screen.findByText(m.assignment_none_found())).toBeTruthy();
		await rerender({ query: 'geführt' });
		expect(screen.getByText('Rechnungsnummer')).toBeTruthy();
		await rerender({ query: 'field.rechnungs' });
		expect(screen.getByText('Rechnungsnummer')).toBeTruthy();
	});

	it('on a phone opens the editor with a tap on the row, which has no buttons of its own', async () => {
		phone.override(true);
		render(WithDialogs, { component: FieldsPanel });
		const name = await screen.findByText('Rechnungsnummer');
		// No pencil, no bin: the row is the button.
		expect(
			screen.queryByRole('button', { name: m.master_edit({ name: 'Rechnungsnummer' }) })
		).toBeNull();
		expect(
			screen.queryByRole('button', { name: m.master_delete({ name: 'Rechnungsnummer' }) })
		).toBeNull();
		// The key stays in the editor; the row leaves it out.
		expect(screen.queryByText('field.rechnungsnummer')).toBeNull();
		await userEvent.click(name.closest('button')!);
		expect(await screen.findByDisplayValue('Rechnungsnummer')).toBeTruthy();
		expect(screen.getByRole('button', { name: m.editor_save_short() })).toBeTruthy();
	});

	it('deletes a field from its editor, after asking', async () => {
		render(WithDialogs, { component: FieldsPanel });
		await screen.findByText('Rechnungsnummer');
		await userEvent.click(
			screen.getByRole('button', { name: m.master_edit({ name: 'Rechnungsnummer' }) })
		);
		await userEvent.click(
			await screen.findByRole('button', { name: m.master_delete({ name: 'Rechnungsnummer' }) })
		);
		const confirm = await screen.findByRole('alertdialog');
		await userEvent.click(within(confirm).getByRole('button', { name: m.delete_title() }));
		const deleted = fetchMock.mock.calls
			.map((call) => call[0])
			.find((call) => call.method === 'DELETE');
		expect(new URL(deleted!.url).pathname).toBe('/api/v1/fields/f1');
	});
});
