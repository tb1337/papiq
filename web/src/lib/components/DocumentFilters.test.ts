import { render, screen } from '@testing-library/svelte';
import { userEvent } from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { NO_FILTERS, type Filters } from '#lib/filters.ts';
import type { Lookup, Tag } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import DocumentFilters from './DocumentFilters.svelte';

function topic(id: string, path: string[], parent: string | null) {
	return {
		id,
		name: path[path.length - 1],
		path,
		parent_id: parent,
		scope: 'topic',
		assignment: 'auto',
		shown: true,
		created_at: '2026-10-17T12:00:00Z'
	} as Tag;
}

const lookup = {
	contacts: [],
	documentTypes: [],
	tags: [
		topic('p1', ['Arbeit'], null),
		topic('p2', ['Versicherung'], null),
		topic('p3', ['Versicherung', 'Kranken'], 'p2')
	],
	fields: [],
	spaces: [],
	users: []
} as unknown as Lookup;

describe('DocumentFilters', () => {
	it('picks several topics as square chips, each one of them', async () => {
		const user = userEvent.setup();
		const onchange = vi.fn<(filters: Filters) => void>();
		render(DocumentFilters, { filters: { ...NO_FILTERS, topics: ['p1'] }, lookup, onchange });
		const remove = screen.getByRole('button', { name: m.filter_topic_remove({ name: 'Arbeit' }) });
		expect(remove.closest('span')?.className).toContain('rounded-md');
		const add = screen.getByRole('combobox', { name: m.filter_topic_add() });
		const offered = [...add.querySelectorAll('option')].map((option) => option.textContent);
		expect(offered).toEqual([m.filter_topic_add(), 'Versicherung', 'Versicherung › Kranken']);
		await user.selectOptions(add, 'p3');
		expect(onchange).toHaveBeenLastCalledWith({ ...NO_FILTERS, topics: ['p1', 'p3'] });
		await user.click(remove);
		expect(onchange).toHaveBeenLastCalledWith({ ...NO_FILTERS, topics: [] });
	});
});
