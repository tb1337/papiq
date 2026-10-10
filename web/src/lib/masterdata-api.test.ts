import { describe, expect, it } from 'vitest';
import { parseAliases } from '#lib/masterdata-api.ts';

describe('parseAliases', () => {
	it('takes one alias per line and drops empty lines', () => {
		expect(parseAliases(' Nord AG \n\n  \nNord KV')).toEqual(['Nord AG', 'Nord KV']);
		expect(parseAliases('')).toEqual([]);
	});
});
