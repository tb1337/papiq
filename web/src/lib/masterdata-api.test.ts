import { describe, expect, it } from 'vitest';
import { parseAliases } from '#lib/masterdata-api.ts';

describe('parseAliases', () => {
	it('takes one alias per line and drops empty lines', () => {
		expect(parseAliases(' INTER AG \n\n  \nINTER KV')).toEqual(['INTER AG', 'INTER KV']);
		expect(parseAliases('')).toEqual([]);
	});
});
