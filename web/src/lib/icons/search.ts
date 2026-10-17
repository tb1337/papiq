/**
 * Finding icons: those offered first by their name and their words in either language, then
 * every other Lucide icon by its name. Words of a query must all occur ("heart pulse" finds
 * `heart-pulse`, "versicherung" finds "Krankenversicherung"); an icon whose words start with
 * them comes before one that only contains them ("car" finds `car` before `credit-card`).
 */
import { CURATED_NAMES } from '#lib/icons/curated.ts';
import { ICON_NAMES } from '#lib/icons/lucide.ts';
import { WORDS as DE } from '#lib/icons/words.de.ts';
import { WORDS as EN } from '#lib/icons/words.en.ts';

/** At most this many icons are shown for a query; a longer list asks for more words. */
export const SEARCH_LIMIT = 96;

const CURATED_SET = new Set(CURATED_NAMES);

function terms(query: string): string[] {
	return query
		.toLocaleLowerCase()
		.split(/[\s-]+/)
		.filter((word) => word !== '');
}

/** How well `text` matches: 0 its name is the query, 1 every word is one of its words, 2
 * every word starts one of them, 3 every word stands in it, null not at all. */
function rank(name: string, text: string, words: readonly string[]): number | null {
	const haystack = text.toLocaleLowerCase();
	if (!words.every((word) => haystack.includes(word))) return null;
	if (name === words.join('-')) return 0;
	const own = terms(haystack);
	if (words.every((word) => own.includes(word))) return 1;
	return words.every((word) => own.some((part) => part.startsWith(word))) ? 2 : 3;
}

/** The names among `names` that match, best first (then in their order). */
function ranked(
	names: readonly string[],
	words: readonly string[],
	text: (name: string) => string
): string[] {
	return names
		.map((name, index) => ({ name, index, rank: rank(name, text(name), words) }))
		.filter((item) => item.rank !== null)
		.sort((a, b) => a.rank! - b.rank! || a.index - b.index)
		.map((item) => item.name);
}

/** The icons for `query`, best first: the curated ones, then the others by name; `total`
 * counts them all. */
export function searchIcons(
	query: string,
	limit = SEARCH_LIMIT
): { names: string[]; total: number } {
	const words = terms(query);
	if (words.length === 0) return { names: [], total: 0 };
	const curated = ranked(
		CURATED_NAMES,
		words,
		(name) => `${name} ${DE[name] ?? ''} ${EN[name] ?? ''}`
	);
	const others = ranked(
		ICON_NAMES.filter((name) => !CURATED_SET.has(name)),
		words,
		(name) => name
	);
	const all = [...curated, ...others];
	return { names: all.slice(0, limit), total: all.length };
}
