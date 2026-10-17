/**
 * Tags as trees, as the API lists them (`GET /tags`: each after its parent, siblings by name,
 * with its path): the tags of a scope, their paths, the tree of a master data tab, and what a
 * search matches. Documents show the leaf; the path says where it stands.
 */
import type { Tag, TagScope } from '#lib/masterdata.svelte.ts';

export const SEPARATOR = ' › ';

/** What the helpers need of a tag. */
export type TagLike = Pick<Tag, 'id' | 'name' | 'scope' | 'parent_id' | 'path'>;

/** "Insurance › Health". */
export function pathLabel(tag: Pick<Tag, 'name' | 'path'>): string {
	return (tag.path.length > 0 ? tag.path : [tag.name]).join(SEPARATOR);
}

/** The tags of `scope`, in the order of the tree. */
export function ofScope<T extends TagLike>(tags: readonly T[], scope: TagScope): T[] {
	return tags.filter((tag) => tag.scope === scope);
}

/** Options of a picker: the tags of `scope` labelled with their path. */
export function tagOptions(
	tags: readonly TagLike[],
	scope: TagScope
): { value: string; label: string }[] {
	return ofScope(tags, scope).map((tag) => ({ value: tag.id, label: pathLabel(tag) }));
}

/** Whether every word of `query` stands in the tag's path: a search for a parent's name finds
 * the tags below it. */
export function matchesQuery(tag: Pick<Tag, 'name' | 'path'>, query: string): boolean {
	const path = pathLabel(tag).toLocaleLowerCase();
	return query
		.toLocaleLowerCase()
		.split(/\s+/)
		.filter((word) => word !== '')
		.every((word) => path.includes(word));
}

/** The ids of the tags below `id` (not `id` itself). */
export function below(tags: readonly TagLike[], id: string): Set<string> {
	const found = new Set<string>();
	let added = true;
	while (added) {
		added = false;
		for (const tag of tags) {
			if (
				tag.parent_id &&
				(tag.parent_id === id || found.has(tag.parent_id)) &&
				!found.has(tag.id)
			) {
				found.add(tag.id);
				added = true;
			}
		}
	}
	return found;
}

/** The tags of a tree by their parent (null: the top), in the order of the list. */
export function childrenOf<T extends TagLike>(tags: readonly T[]): Map<string | null, T[]> {
	const ids = new Set(tags.map((tag) => tag.id));
	const children = new Map<string | null, T[]>();
	for (const tag of tags) {
		const parent = tag.parent_id && ids.has(tag.parent_id) ? tag.parent_id : null;
		children.set(parent, [...(children.get(parent) ?? []), tag]);
	}
	return children;
}

/** The rows of a tree view: each tag with its depth, below a collapsed tag nothing. Tags no
 * top-level tag leads to (a cycle in stored data) come last, as if at the top, so that they stay
 * visible and can be fixed. */
export function visibleRows<T extends TagLike>(
	tags: readonly T[],
	collapsed: ReadonlySet<string>
): { tag: T; depth: number; hasChildren: boolean }[] {
	const children = childrenOf(tags);
	const reached = new Set<string>();
	const reach = (parent: string | null) => {
		for (const tag of children.get(parent) ?? []) {
			if (reached.has(tag.id)) continue;
			reached.add(tag.id);
			reach(tag.id);
		}
	};
	reach(null);
	const rows: { tag: T; depth: number; hasChildren: boolean }[] = [];
	const seen = new Set<string>();
	const show = (tag: T, depth: number) => {
		if (seen.has(tag.id)) return;
		seen.add(tag.id);
		const kids = children.get(tag.id) ?? [];
		rows.push({ tag, depth, hasChildren: kids.length > 0 });
		if (!collapsed.has(tag.id)) for (const kid of kids) show(kid, depth + 1);
	};
	for (const tag of children.get(null) ?? []) show(tag, 0);
	for (const tag of tags) {
		if (reached.has(tag.id)) continue;
		reach(tag.id);
		reached.add(tag.id);
		show(tag, 0);
	}
	return rows;
}

/** `ids` without the tags above others among them: a child and its parent set together leave
 * the child, as the API keeps them. */
export function normalise(tags: readonly TagLike[], ids: readonly string[]): string[] {
	const higher = above(tags, ids);
	return ids.filter((id, index) => !higher.has(id) && ids.indexOf(id) === index);
}

/** The chips of `ids`: for each, the nearest shown tag on its way up (itself, else a tag above
 * it), each once, by path. A hidden tag shows nothing unless a tag above it is shown. */
export function shownChips<T extends TagLike & Pick<Tag, 'shown'>>(
	tags: readonly T[],
	ids: readonly string[]
): T[] {
	const byId = new Map(tags.map((tag) => [tag.id, tag]));
	const found = new Map<string, T>();
	for (const id of ids) {
		const seen = new Set<string>();
		let tag = byId.get(id);
		while (tag && !tag.shown && tag.parent_id && !seen.has(tag.id)) {
			seen.add(tag.id);
			tag = byId.get(tag.parent_id);
		}
		if (tag?.shown) found.set(tag.id, tag);
	}
	return [...found.values()].sort((a, b) => pathLabel(a).localeCompare(pathLabel(b)));
}

/** The ids of the tags above those of `ids`. */
export function above(tags: readonly TagLike[], ids: readonly string[]): Set<string> {
	const parentOf = new Map(tags.map((tag) => [tag.id, tag.parent_id]));
	const found = new Set<string>();
	for (const id of ids) {
		let parent = parentOf.get(id) ?? null;
		while (parent !== null && !found.has(parent)) {
			found.add(parent);
			parent = parentOf.get(parent) ?? null;
		}
	}
	return found;
}
