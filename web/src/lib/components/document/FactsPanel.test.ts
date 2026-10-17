import { fireEvent, render, screen, waitFor, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Facts } from '#lib/facts.ts';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import FactsPanel from './FactsPanel.svelte';

const UTILITY = 'contact-1';
const BANK = 'contact-2';
const FIELD = 'field-1';

const lookup = {
	contacts: [
		{ id: UTILITY, name: 'Stadtwerke Musterstadt GmbH' },
		{ id: BANK, name: 'Bank Musterstadt' }
	],
	documentTypes: [],
	tags: [],
	fields: [{ id: FIELD, name: 'Rechnungsbetrag' }],
	spaces: [],
	users: []
} as unknown as Lookup;

const FACTS: Facts = {
	contact_id: UTILITY,
	named: [
		{
			contact_id: UTILITY,
			name: 'Stadtwerke Musterstadt GmbH',
			alias: false,
			in_text: true,
			in_code: false,
			spans: [{ start: 0, end: 22 }]
		}
	],
	identifiers: [
		{
			kind: 'iban',
			value: 'DE89370400440532013000',
			contact_id: UTILITY,
			in_text: true,
			in_code: true,
			spans: [{ start: 40, end: 67 }],
			other_contacts: 0
		},
		{
			kind: 'customer_number',
			value: '20047718',
			contact_id: null,
			in_text: true,
			in_code: false,
			spans: [{ start: 80, end: 89 }],
			other_contacts: 0
		},
		{
			kind: 'iban',
			value: 'DE02120300000000202051',
			contact_id: null,
			in_text: true,
			in_code: false,
			spans: [{ start: 100, end: 127 }],
			other_contacts: 6
		}
	],
	codes: [
		{
			format: 'QR Code',
			text: 'BCD\n002',
			places: [{ page: 1, boxes: [{ x: 0.7, y: 0.8, width: 0.1, height: 0.1 }] }],
			giro: {
				name: 'Stadtwerke Musterstadt GmbH',
				iban: 'DE89370400440532013000',
				bic: 'MUSTDEM1XXX',
				amount: { amount: '84.20', currency: 'EUR' },
				purpose: 'Abschlag 03/2026'
			},
			identifiers: [{ kind: 'iban', value: 'DE89370400440532013000' }],
			fields: [FIELD]
		},
		{
			format: 'Data Matrix',
			text: 'DEA5E12C0001',
			// The same code on two pages: one entry.
			places: [
				{ page: 2, boxes: [{ x: 0.8, y: 0.05, width: 0.1, height: 0.05 }] },
				{ page: 4, boxes: [{ x: 0.8, y: 0.05, width: 0.1, height: 0.05 }] }
			],
			giro: null,
			identifiers: [],
			fields: []
		}
	]
};

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

function props(overrides: Record<string, unknown> = {}) {
	return {
		facts: FACTS,
		documentId: 'doc-1',
		lookup,
		admin: true,
		onmark: vi.fn(),
		onshowcode: vi.fn(),
		onchanged: vi.fn(),
		...overrides
	};
}

beforeEach(() => {
	fetchMock.mockReset();
	fetchMock.mockImplementation(async () => new Response('{}', { status: 200 }));
	vi.stubGlobal('fetch', fetchMock);
});

afterEach(() => {
	vi.unstubAllGlobals();
});

describe('FactsPanel', () => {
	it('shows what supports the contact, what has no contact, and the codes', () => {
		render(FactsPanel, props());
		const own = screen.getByRole('heading', { name: /Stadtwerke Musterstadt GmbH/ });
		expect(own.textContent).toContain(m.facts_own());
		// The IBAN as a fact, and as the GiroCode's.
		expect(screen.getAllByText('DE89 3704 0044 0532 0130 00')).toHaveLength(2);
		expect(screen.getByText(m.facts_unknown())).toBeTruthy();
		expect(screen.getByText('20047718')).toBeTruthy();
		// The owner's own IBAN stands on many contacts' documents.
		expect(screen.getByText(m.facts_other_contacts({ count: 6 }))).toBeTruthy();
		expect(screen.getAllByRole('button', { name: m.facts_attach() })).toHaveLength(2);
		// The GiroCode read, its amount as the field's; another code as it is.
		expect(screen.getByText(m.code_giro())).toBeTruthy();
		expect(screen.getByText(m.code_same_as({ field: 'Rechnungsbetrag' }))).toBeTruthy();
		expect(screen.getByText('DEA5E12C0001')).toBeTruthy();
	});

	it('marks a fact in the text while hovered, and keeps it on a click', async () => {
		const onmark = vi.fn();
		render(FactsPanel, props({ onmark }));
		const customer = screen.getByRole('button', { name: /20047718/ });
		await fireEvent.mouseEnter(customer.parentElement!.parentElement!);
		expect(onmark).toHaveBeenLastCalledWith([{ start: 80, end: 89 }], false);
		await fireEvent.click(customer);
		expect(onmark).toHaveBeenLastCalledWith([{ start: 80, end: 89 }], true);
		expect(customer.getAttribute('aria-pressed')).toBe('true');
	});

	it('marks what of a code stands in the text, and shows a code on each of its pages', async () => {
		const onmark = vi.fn();
		const onshowcode = vi.fn();
		render(FactsPanel, props({ onmark, onshowcode }));
		await fireEvent.click(
			screen.getByRole('button', { name: m.codes_mark({ format: 'QR Code' }) })
		);
		expect(onmark).toHaveBeenLastCalledWith([{ start: 40, end: 67 }], true);
		await fireEvent.click(screen.getByRole('button', { name: m.source_jump({ page: 1 }) }));
		expect(onshowcode).toHaveBeenLastCalledWith(FACTS.codes![0], FACTS.codes![0].places[0]);
		const matrix = FACTS.codes![1];
		expect(screen.getAllByRole('button', { name: m.source_jump({ page: 2 }) })).toHaveLength(1);
		await fireEvent.click(screen.getByRole('button', { name: m.source_jump({ page: 4 }) }));
		expect(onshowcode).toHaveBeenLastCalledWith(matrix, matrix.places[1]);
	});

	it('lets an admin attach an identifier to the document contact', async () => {
		const onchanged = vi.fn();
		render(FactsPanel, props({ onchanged }));
		const user = userEvent.setup();
		await user.click(screen.getAllByRole('button', { name: m.facts_attach() })[0]);
		const form = screen.getByText(
			m.facts_attach_title({ kind: m.identifier_kind_customer_number() })
		).parentElement!;
		expect(
			within(form)
				.getByRole('radio', { name: /Stadtwerke Musterstadt GmbH/ })
				.getAttribute('value')
		).toBe('document');
		await user.click(within(form).getByRole('button', { name: m.facts_attach() }));
		await waitFor(() => expect(onchanged).toHaveBeenCalled());
		const [request] = fetchMock.mock.calls[0];
		expect(new URL(request.url).pathname).toBe(`/api/v1/contacts/${UTILITY}/identifiers`);
		expect(await request.json()).toEqual({ kind: 'customer_number', value: '20047718' });
	});

	it("opens the contact's dialog from its name, for admins", async () => {
		const open = vi.spyOn(masterDataDialogs, 'open').mockImplementation(() => {});
		render(FactsPanel, props());
		const own = screen.getByRole('heading', { name: /Stadtwerke Musterstadt GmbH/ });
		await fireEvent.click(within(own).getByRole('button', { name: 'Stadtwerke Musterstadt GmbH' }));
		expect(open).toHaveBeenCalledWith('contacts', UTILITY);
		open.mockRestore();
	});

	it('offers nothing to attach to those who are no admins', () => {
		render(FactsPanel, props({ admin: false }));
		const own = screen.getByRole('heading', { name: /Stadtwerke Musterstadt GmbH/ });
		expect(within(own).queryByRole('button')).toBeNull();
		expect(screen.queryByRole('button', { name: m.facts_attach() })).toBeNull();
		expect(screen.getByText(m.facts_attach_admins())).toBeTruthy();
	});

	it('says when the codes are not read yet', () => {
		render(FactsPanel, props({ facts: { ...FACTS, codes: null } }));
		expect(screen.getByText(m.codes_unread())).toBeTruthy();
	});
});
