// Every text exists in every language, with the same placeholders.
import { describe, expect, it } from 'vitest';
import de from '../messages/de.json';
import en from '../messages/en.json';

const catalogues: Record<string, Record<string, string>> = { de, en };

function keys(catalogue: Record<string, string>): string[] {
	return Object.keys(catalogue)
		.filter((key) => key !== '$schema')
		.sort();
}

function placeholders(text: string): string[] {
	return [...text.matchAll(/\{(\w+)\}/g)].map((match) => match[1]).sort();
}

describe('messages', () => {
	it('have the same keys in every language', () => {
		expect(keys(de)).toEqual(keys(en));
	});

	it.each(Object.keys(catalogues))('are not empty in %s', (locale) => {
		const catalogue = catalogues[locale];
		const empty = keys(catalogue).filter((key) => !catalogue[key].trim());
		expect(empty).toEqual([]);
	});

	it('use the same placeholders in every language', () => {
		const differing = keys(en).filter(
			(key) =>
				placeholders(en[key as keyof typeof en]).join() !==
				placeholders(de[key as keyof typeof de]).join()
		);
		expect(differing).toEqual([]);
	});
});
