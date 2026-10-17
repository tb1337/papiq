import { render, screen } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import IdentifiersEditor from '#lib/components/IdentifiersEditor.svelte';
import { changesOf, rowsOf } from '#lib/identifiers.ts';
import { m } from '#lib/paraglide/messages.js';

const stored = [
	{ kind: 'iban', value: 'DE89370400440532013000', source: 'manual', topic_id: null },
	{ kind: 'email_domain', value: 'stadtwerke.example', source: 'learned', topic_id: null },
	{ kind: 'customer_number', value: '4711', source: 'rejected', topic_id: null }
] as const;

describe('identifiers', () => {
	it('sends the entered and the rejected ones, not the learned ones', () => {
		const rows = rowsOf([...stored]);
		rows.push({ ...rows[0], key: 99, value: '  ' });
		expect(changesOf(rows)).toEqual({
			identifiers: [{ kind: 'iban', value: 'DE89370400440532013000' }],
			rejected_identifiers: [{ kind: 'customer_number', value: '4711' }]
		});
	});

	it('takes over or rejects a learned one, and allows a rejected one again', async () => {
		const rows = rowsOf([...stored]);
		render(IdentifiersEditor, { rows });
		expect(screen.getByText(m.identifier_learned())).toBeTruthy();
		await userEvent.click(screen.getByRole('button', { name: m.identifier_reject() }));
		expect(screen.getAllByText(m.identifier_rejected())).toHaveLength(2);
		await userEvent.click(screen.getAllByRole('button', { name: m.identifier_unblock() })[0]);
		expect(screen.getAllByText(m.identifier_rejected())).toHaveLength(1);
		await userEvent.click(screen.getByRole('button', { name: m.identifier_add() }));
		expect(screen.getAllByRole('textbox', { name: m.identifier_value() })).toHaveLength(2);
	});
});

describe('the topic of an identifier', () => {
	it('is sent where the contact enables it, never with a rejected one', () => {
		const rows = rowsOf([
			{ kind: 'personnel_number', value: '4711', source: 'manual', topic_id: 'p1' },
			{ kind: 'customer_number', value: 'KV-1', source: 'manual', topic_id: 'p2' },
			{ kind: 'iban', value: 'DE89370400440532013000', source: 'rejected', topic_id: null }
		]);
		expect(changesOf(rows, ['p1'])).toEqual({
			identifiers: [
				{ kind: 'personnel_number', value: '4711', topic_id: 'p1' },
				{ kind: 'customer_number', value: 'KV-1' }
			],
			rejected_identifiers: [{ kind: 'iban', value: 'DE89370400440532013000' }]
		});
	});
});
