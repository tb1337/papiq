/**
 * The Tags tab of the master data: every kind of tag in one place. The kind filter stands in
 * the URL (`?tab=tags&kind=contact-groups`, none: all kinds); which kinds' sections are
 * collapsed is remembered in this browser (a convenience: without storage all are open). A
 * search matches a tag's path, so a parent's name finds the tags below it; the tree shows each
 * match with the tags above it.
 */
import type { TagKind } from '#lib/masterdata-api.ts';
import type { Tag } from '#lib/masterdata.svelte.ts';
import { above, matchesQuery } from '#lib/tags.ts';

/** The kinds in the order of their sections. */
export const TAG_KIND_ORDER: readonly TagKind[] = [
	'tags',
	'contact-groups',
	'type-groups',
	'topics'
];

const COLLAPSED_KEY = 'papiq.master-data.tags.collapsed';

/** The kind the URL filters by; null (all kinds) for none or an unknown one. */
export function kindFilter(params: Pick<URLSearchParams, 'get'>): TagKind | null {
	const kind = params.get('kind');
	return (TAG_KIND_ORDER as readonly string[]).includes(kind ?? '') ? (kind as TagKind) : null;
}

/** The kinds whose sections this browser keeps collapsed. */
export function rememberedCollapsed(): TagKind[] {
	try {
		const stored = JSON.parse(globalThis.localStorage?.getItem(COLLAPSED_KEY) ?? '[]');
		return Array.isArray(stored) ? TAG_KIND_ORDER.filter((kind) => stored.includes(kind)) : [];
	} catch {
		return []; // no storage or garbage in it: all open
	}
}

export function rememberCollapsed(kinds: Iterable<TagKind>): void {
	try {
		const list = TAG_KIND_ORDER.filter((kind) => [...kinds].includes(kind));
		if (list.length === 0) globalThis.localStorage?.removeItem(COLLAPSED_KEY);
		else globalThis.localStorage?.setItem(COLLAPSED_KEY, JSON.stringify(list));
	} catch {
		// Kept for this page only.
	}
}

/** What a search shows of `tags` (one tree): the matches and the tags above them, in the order
 * of the tree; all of them without a query. */
export function searched<T extends Tag>(tags: readonly T[], query: string): T[] {
	if (query.trim() === '') return [...tags];
	const matches = tags.filter((tag) => matchesQuery(tag, query)).map((tag) => tag.id);
	const kept = new Set([...matches, ...above(tags, matches)]);
	return tags.filter((tag) => kept.has(tag.id));
}

/** The number of tags right below each tag. */
export function childCounts(tags: readonly Pick<Tag, 'parent_id'>[]): Map<string, number> {
	const counts = new Map<string, number>();
	for (const tag of tags) {
		if (tag.parent_id) counts.set(tag.parent_id, (counts.get(tag.parent_id) ?? 0) + 1);
	}
	return counts;
}
