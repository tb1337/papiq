import { describe, expect, it } from 'vitest';
import { CURATED, CURATED_NAMES } from './curated.ts';
import { CURATED_MODULE_NAMES, curatedModule } from './curated-modules.ts';
import { loadIcon as loadAny } from './load.ts';
import { ICON_NAMES, isIcon, loadIcon } from './lucide.ts';
import { SEARCH_LIMIT, searchIcons } from './search.ts';
import { WORDS as DE } from './words.de.ts';
import { WORDS as EN } from './words.en.ts';

describe('icons', () => {
	it('come from the build, each loaded on its own', async () => {
		expect(ICON_NAMES.length).toBe(1866);
		expect(isIcon('house') && !isIcon('no-such-icon')).toBe(true);
		expect(await loadIcon('house')).not.toBeNull();
		expect(await loadIcon('no-such-icon')).toBeNull();
	});

	it('offer 170 known ones by eleven areas, with words in both languages', () => {
		expect(CURATED.length).toBe(11);
		expect(CURATED_NAMES.length).toBe(170);
		expect(new Set(CURATED_NAMES).size).toBe(170);
		expect(CURATED_NAMES.filter((name) => !isIcon(name))).toEqual([]);
		expect(Object.keys(DE).sort()).toEqual([...CURATED_NAMES].sort());
		expect(Object.keys(EN).sort()).toEqual([...CURATED_NAMES].sort());
	});

	it('offered first load from a small map of their own, any other from the index', async () => {
		expect([...CURATED_MODULE_NAMES].sort()).toEqual([...CURATED_NAMES].sort());
		expect(curatedModule('piggy-bank')).toBeDefined();
		expect(curatedModule('rocket')).toBeUndefined();
		expect(await loadAny('piggy-bank')).toBe(await loadIcon('piggy-bank'));
		expect(await loadAny('rocket')).toBe(await loadIcon('rocket'));
		expect(await loadAny('no-such-icon')).toBeNull();
	});

	it('are found by words in either language, then by their name', () => {
		expect(searchIcons('Auto').names.slice(0, 2)).toEqual(['car', 'car-front']);
		expect(searchIcons('car').names.slice(0, 2)).toEqual(['car', 'car-front']);
		expect(searchIcons('heart pulse').names[0]).toBe('heart-pulse');
		expect(searchIcons('Steuer').names).toContain('calculator');
		// Beyond the ones offered first, every Lucide icon by its name.
		expect(searchIcons('rocket').names).toContain('rocket');
		expect(searchIcons('  ').names).toEqual([]);
		expect(searchIcons('zzzz-nothing').total).toBe(0);
		const many = searchIcons('a');
		expect(many.names.length).toBe(SEARCH_LIMIT);
		expect(many.total).toBeGreaterThan(SEARCH_LIMIT);
	});
});
