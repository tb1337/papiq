import { describe, expect, it } from 'vitest';
import { contactMatches, fieldMatches, typeMatches } from './master-search.ts';

describe('the search of the master data', () => {
	it('finds a contact by name, alias and identifier, not by a rejected one', () => {
		const contact = {
			name: 'Sparkasse Musterstadt',
			aliases: ['SPK'],
			identifiers: [
				{
					kind: 'iban' as const,
					value: 'DE02120300000000202051',
					source: 'manual' as const,
					topic_id: null
				},
				{
					kind: 'customer_number' as const,
					value: 'X-99',
					source: 'rejected' as const,
					topic_id: null
				}
			]
		};
		expect(contactMatches(contact, 'muster spar')).toBe(true);
		expect(contactMatches(contact, 'spk')).toBe(true);
		expect(contactMatches(contact, 'DE02 1203 0000 0000 2020 51')).toBe(true);
		expect(contactMatches(contact, 'x-99')).toBe(false);
	});

	it('finds a type by its description and a field by its key', () => {
		expect(typeMatches({ name: 'Rechnung', description: 'nicht Mahnungen' }, 'mahnung')).toBe(true);
		expect(typeMatches({ name: 'Rechnung', description: null }, 'mahnung')).toBe(false);
		const field = { name: 'LA_Brutto', key: 'la_brutto', description: null };
		expect(fieldMatches(field, 'field.la_')).toBe(true);
		expect(fieldMatches(field, 'netto')).toBe(false);
	});
});
