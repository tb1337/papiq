/** How a contact shows without a logo: its initials. */

// Legal forms say nothing about whom one deals with.
const LEGAL_FORMS = new Set([
	'ag',
	'co',
	'eg',
	'ek',
	'ev',
	'gbr',
	'gmbh',
	'inc',
	'kg',
	'kgaa',
	'llc',
	'ltd',
	'mbh',
	'ohg',
	'plc',
	'sa',
	'se',
	'ug'
]);

/** Up to two letters: of the first and the last word of the name, without legal forms and
 * abbreviations ("Praxis Dr. Berg": PB); one word gives one letter. */
export function initials(name: string): string {
	const all = name.split(/[\s&+/,()-]+/).filter((word) => /^[\p{L}\p{N}]/u.test(word));
	const words = all.filter(
		(word) => !LEGAL_FORMS.has(word.toLocaleLowerCase().replaceAll('.', '')) && !word.endsWith('.')
	);
	const kept = words.length > 0 ? words : all;
	if (kept.length === 0) return '?';
	const first = Array.from(kept[0])[0];
	const last = kept.length > 1 ? Array.from(kept[kept.length - 1])[0] : '';
	return (first + last).toLocaleUpperCase();
}
