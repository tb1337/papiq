import { describe, expect, it } from 'vitest';
import { initials } from './contacts.ts';

describe('initials', () => {
	it.each([
		['Nord Versicherungsgruppe', 'NV'],
		['Stadtwerke Süd', 'SS'],
		['Praxis Dr. Berg', 'PB'],
		['Stadtwerke München GmbH', 'SM'],
		['ACME', 'A'],
		['ärztekammer nordrhein', 'ÄN'],
		['Müller & Söhne KG', 'MS'],
		['GmbH', 'G'],
		['1&1 Telecom', '1T'],
		['...', '?']
	])('of %s is %s', (name, expected) => {
		expect(initials(name)).toBe(expected);
	});
});
