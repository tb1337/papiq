import { describe, expect, it } from 'vitest';
import type { Tag } from '#lib/masterdata.svelte.ts';
import {
	below,
	matchesQuery,
	normalise,
	ofScope,
	pathLabel,
	SEPARATOR,
	shownChips,
	tagOptions,
	visibleRows
} from './tags.ts';

function tag(id: string, path: string[], parent: string | null, scope = 'document'): Tag {
	return {
		id,
		name: path[path.length - 1],
		path,
		parent_id: parent,
		scope: scope as Tag['scope'],
		assignment: scope === 'document' ? 'auto' : 'manual',
		shown: scope === 'document',
		icon: null,
		color: null,
		created_at: '2026-10-16T12:00:00Z'
	};
}

// As the API lists them: each after its parent, siblings by name.
const TAGS = [
	tag('bills', ['Bills'], null),
	tag('insurance', ['Insurance'], null),
	tag('car', ['Insurance', 'Car'], 'insurance'),
	tag('health', ['Insurance', 'Health'], 'insurance'),
	tag('dental', ['Insurance', 'Health', 'Dental'], 'health'),
	tag('insurer', ['Insurer'], null, 'contact')
];

describe('tags as trees', () => {
	it('label a tag with its path and pick by scope', () => {
		expect(pathLabel(TAGS[4])).toBe(['Insurance', 'Health', 'Dental'].join(SEPARATOR));
		expect(ofScope(TAGS, 'contact').map((entry) => entry.id)).toEqual(['insurer']);
		expect(tagOptions(TAGS, 'document')[2]).toEqual({
			value: 'car',
			label: `Insurance${SEPARATOR}Car`
		});
	});

	it('find a tag by the name of a tag above it', () => {
		const found = TAGS.filter((entry) => matchesQuery(entry, 'insur hea'));
		expect(found.map((entry) => entry.id)).toEqual(['health', 'dental']);
		expect(TAGS.filter((entry) => matchesQuery(entry, '')).length).toBe(TAGS.length);
	});

	it('know what is below a tag', () => {
		expect(below(TAGS, 'insurance')).toEqual(new Set(['car', 'health', 'dental']));
		expect(below(TAGS, 'dental').size).toBe(0);
	});

	it('show rows with their depth, nothing below a collapsed tag', () => {
		const rows = (collapsed: string[]) =>
			visibleRows(ofScope(TAGS, 'document'), new Set(collapsed)).map(
				(row) => `${row.depth}:${row.tag.id}${row.hasChildren ? '+' : ''}`
			);
		expect(rows([])).toEqual(['0:bills', '0:insurance+', '1:car', '1:health+', '2:dental']);
		expect(rows(['health'])).toEqual(['0:bills', '0:insurance+', '1:car', '1:health+']);
		expect(rows(['insurance'])).toEqual(['0:bills', '0:insurance+']);
	});

	it('show tags no top-level tag leads to at the top', () => {
		const cycle = [tag('top', ['Top'], null), tag('a', ['B', 'A'], 'b'), tag('b', ['A', 'B'], 'a')];
		const rows = visibleRows(cycle, new Set()).map((row) => `${row.depth}:${row.tag.id}`);
		expect(rows).toEqual(['0:top', '0:a', '1:b']);
	});
});

describe('tags at a document', () => {
	it('keep the child of a parent set with it', () => {
		expect(normalise(TAGS, ['insurance', 'dental', 'bills'])).toEqual(['dental', 'bills']);
		expect(normalise(TAGS, ['insurance'])).toEqual(['insurance']);
		expect(normalise(TAGS, ['car', 'car', 'unknown'])).toEqual(['car', 'unknown']);
	});

	it('show the nearest shown tag on the way up, each once', () => {
		const hidden = TAGS.map((entry) =>
			entry.id === 'dental' || entry.id === 'car' ? { ...entry, shown: false } : entry
		);
		expect(shownChips(hidden, ['dental', 'bills']).map((entry) => entry.id)).toEqual([
			'bills',
			'health'
		]);
		expect(shownChips(hidden, ['car', 'health']).map((entry) => entry.id)).toEqual([
			'insurance',
			'health'
		]);
		const insurer = TAGS[5]; // a group, hidden by default
		expect(shownChips(TAGS, [insurer.id])).toEqual([]);
	});
});
