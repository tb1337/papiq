import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Tag } from '#lib/masterdata.svelte.ts';
import {
	childCounts,
	kindFilter,
	rememberCollapsed,
	rememberedCollapsed,
	searched
} from './tag-kinds.ts';

const tag = (id: string, path: string[], parent: string | null) =>
	({ id, name: path.at(-1), path, parent_id: parent, scope: 'document' }) as Tag;

const TAGS = [
	tag('a', ['Tax'], null),
	tag('b', ['Tax', 'Tax 2025'], 'a'),
	tag('c', ['Tax', 'Tax 2025', 'Receipts'], 'b'),
	tag('d', ['Health'], null)
];

afterEach(() => {
	localStorage.clear();
	vi.restoreAllMocks();
});

describe('the Tags tab', () => {
	it('reads the kind from the URL, all kinds for none or an unknown one', () => {
		expect(kindFilter(new URLSearchParams('tab=tags&kind=topics'))).toBe('topics');
		expect(kindFilter(new URLSearchParams('tab=tags&kind=persons'))).toBeNull();
		expect(kindFilter(new URLSearchParams('tab=tags'))).toBeNull();
	});

	it('remembers collapsed sections, and forgets garbage or missing storage', () => {
		rememberCollapsed(['topics', 'tags']);
		expect(rememberedCollapsed()).toEqual(['tags', 'topics']);
		localStorage.setItem('papiq.master-data.tags.collapsed', '{"no": 1');
		expect(rememberedCollapsed()).toEqual([]);
		vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
			throw new Error('blocked');
		});
		expect(rememberedCollapsed()).toEqual([]);
	});

	it('searches the paths and keeps the tags above a match', () => {
		expect(searched(TAGS, 'receipts').map((found) => found.id)).toEqual(['a', 'b', 'c']);
		expect(searched(TAGS, 'tax').map((found) => found.id)).toEqual(['a', 'b', 'c']);
		expect(searched(TAGS, 'health').map((found) => found.id)).toEqual(['d']);
		expect(searched(TAGS, '  ')).toHaveLength(4);
	});

	it('counts the tags right below each', () => {
		expect([...childCounts(TAGS)]).toEqual([
			['a', 1],
			['b', 1]
		]);
	});
});
