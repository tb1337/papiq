import { render, screen, waitFor } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import type { Lookup, Tag } from '#lib/masterdata.svelte.ts';
import { SEPARATOR } from '#lib/tags.ts';
import NamesLine from './NamesLine.svelte';
import TagChip from './TagChip.svelte';
import TagChips from './TagChips.svelte';

function tag(id: string, path: string[], parent: string | null, scope: string, shown: boolean) {
	return {
		id,
		name: path[path.length - 1],
		path,
		parent_id: parent,
		scope,
		assignment: scope === 'document' ? 'auto' : 'manual',
		shown,
		created_at: '2026-10-16T12:00:00Z'
	} as Tag;
}

const lookup = {
	contacts: [{ id: 'c1', name: 'Nord', group_ids: ['g2'] }],
	documentTypes: [{ id: 'k1', name: 'Invoice', group_ids: ['g3'] }],
	tags: [
		tag('t1', ['Insurance'], null, 'document', true),
		tag('t2', ['Insurance', 'Health'], 't1', 'document', true),
		tag('t3', ['Paid'], null, 'document', false),
		tag('g1', ['Insurer'], null, 'contact', true),
		tag('g2', ['Insurer', 'Health insurer'], 'g1', 'contact', false),
		tag('g3', ['Bills'], null, 'document_type', false),
		tag('p1', ['Versicherung'], null, 'topic', true),
		tag('p2', ['Versicherung', 'Kranken'], 'p1', 'topic', true)
	],
	fields: [],
	spaces: [],
	users: []
} as unknown as Lookup;

describe('TagChips', () => {
	it('shows the shown tags by their own names, the path as tooltip', () => {
		render(TagChips, { document: { tag_ids: ['t2', 't3'] }, lookup });
		expect(screen.getByText('Health').closest('[title]')?.getAttribute('title')).toBe(
			`Insurance${SEPARATOR}Health`
		);
		expect(screen.queryByText('Paid')).toBeNull(); // hidden
	});

	it('shows the topic first, square, the tags round', () => {
		const { container } = render(TagChips, {
			document: { tag_ids: ['t2'], topic_id: 'p2' },
			lookup
		});
		const chips = [...container.querySelectorAll('[title]')];
		expect(chips.map((chip) => chip.textContent?.trim())).toEqual(['Kranken', 'Health']);
		expect(chips[0].className).toContain('rounded-md');
		expect(chips[1].className).toContain('rounded-full');
		expect(chips[0].getAttribute('title')).toBe(`Versicherung${SEPARATOR}Kranken`);
	});
});

describe('NamesLine', () => {
	it('shows the shown groups next to the contact and the type', () => {
		const { container } = render(NamesLine, {
			parts: [
				{ name: 'Nord', groupIds: ['g2'] },
				{ name: 'Invoice', groupIds: ['g3'] }
			],
			date: '16.10.2026',
			tags: lookup.tags
		});
		// The contact's group is hidden, the one above it shown; the type's group is hidden.
		const text = [...container.querySelectorAll('span')]
			.filter((span) => span.children.length === 0)
			.map((span) => span.textContent?.trim());
		expect(text).toEqual(['Nord', 'Insurer', '·', 'Invoice', '·', '16.10.2026']);
		expect(screen.queryByText('Bills')).toBeNull();
	});
});

describe('TagChip', () => {
	it('draws a tag in its colour with its icon', async () => {
		const { container } = render(TagChip, {
			tag: { id: 'x', name: 'Health', path: ['Health'], icon: 'heart-pulse', color: 'pink' }
		});
		const chip = container.querySelector('[title="Health"]') as HTMLElement;
		expect(chip.getAttribute('style')).toContain(
			'--chip-bg: var(--tag-pink); --chip-fg: var(--tag-pink-foreground)'
		);
		expect(chip.className).toContain('bg-(--chip-bg)');
		await waitFor(() => expect(chip.querySelector('svg.lucide-heart-pulse')).not.toBeNull());
	});

	it('mixes an own colour so that the writing stays readable in each theme', () => {
		const { container } = render(TagChip, {
			tag: { id: 'x', name: 'Weiß', path: ['Weiß'], color: '#FFFFFF' },
			topic: true
		});
		const chip = container.querySelector('[title="Weiß"]') as HTMLElement;
		expect(chip.className).toContain('chip-own');
		expect(chip.className).toContain('rounded-md');
		const style = chip.getAttribute('style') ?? '';
		expect(style).toContain('--chip-own: #ffffff');
		const light = Number(/--chip-light: (\d+)%/.exec(style)?.[1]);
		expect(light).toBeLessThan(70);
	});

	it('keeps an uncoloured tag as it was', () => {
		const { container } = render(TagChip, { tag: { id: 'x', name: 'Plain', path: ['Plain'] } });
		const chip = container.querySelector('[title="Plain"]') as HTMLElement;
		expect(chip.getAttribute('style')).toBeNull();
		expect(chip.className).toContain('bg-secondary');
	});
});

describe('NamesLine', () => {
	it('shows the contact with its initials and the type with its icon', async () => {
		const { container } = render(NamesLine, {
			parts: [
				{ name: 'Nord', avatar: { name: 'Nord Versicherung', logo: null } },
				{ name: 'Invoice', icon: 'receipt' }
			],
			tags: []
		});
		expect(container.querySelector('[data-initials]')?.textContent?.trim()).toBe('NV');
		await waitFor(() => expect(container.querySelector('svg.lucide-receipt')).not.toBeNull());
	});
});
