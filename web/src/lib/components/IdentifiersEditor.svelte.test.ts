import { render, screen, within } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it } from 'vitest';
import { kindLabel, row } from '#lib/identifiers.ts';
import type { Tag } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { phone } from '#lib/phone.svelte.ts';
import IdentifiersEditor from './IdentifiersEditor.svelte';

const WORK = {
	id: 'p1',
	name: 'Arbeit',
	path: ['Arbeit'],
	parent_id: null,
	scope: 'topic',
	assignment: 'auto',
	shown: true,
	icon: null,
	color: 'violet',
	created_at: '2026-10-17T12:00:00Z'
} as Tag;

afterEach(() => phone.override(null));

describe('IdentifiersEditor on a phone', () => {
	it('lists identifiers as rows that open in place, and adds one opened', async () => {
		phone.override(true);
		const user = userEvent.setup();
		const props = $state({
			rows: [
				row('personnel_number', '4711', 'manual', 'p1'),
				row('iban', 'DE89370400440532013000', 'learned')
			],
			topics: [WORK]
		});
		render(IdentifiersEditor, props);
		const personnel = screen.getByText('4711').closest('button')!;
		expect(within(personnel).getByText(kindLabel('personnel_number'))).toBeTruthy();
		expect(within(personnel).getByText('Arbeit')).toBeTruthy();
		const learned = screen.getByText('DE89370400440532013000').closest('button')!;
		expect(within(learned).getByText(m.identifier_learned())).toBeTruthy();
		// Closed, the rows have no fields.
		expect(screen.queryByLabelText(m.identifier_value())).toBeNull();
		await user.click(personnel);
		const value = screen.getByLabelText(m.identifier_value()) as HTMLInputElement;
		expect(value.value).toBe('4711');
		await user.clear(value);
		await user.type(value, '0815');
		await user.click(screen.getByRole('button', { name: m.picker_done() }));
		expect(props.rows[0].value).toBe('0815');
		await user.click(screen.getByText('DE89370400440532013000').closest('button')!);
		await user.click(screen.getByRole('button', { name: m.identifier_keep() }));
		expect(props.rows[1].source).toBe('manual');
		await user.click(screen.getByRole('button', { name: m.picker_done() }));
		await user.click(screen.getByRole('button', { name: m.identifier_add() }));
		expect(props.rows).toHaveLength(3);
		expect((screen.getByLabelText(m.identifier_value()) as HTMLInputElement).value).toBe('');
		await user.click(screen.getByRole('button', { name: m.identifier_remove() }));
		expect(props.rows).toHaveLength(2);
	});
});
