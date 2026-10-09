import { describe, expect, it } from 'vitest';
import { emptyValue, sameValue, toApi, toInput } from './fields.ts';

describe('field values', () => {
	it('keeps text and trims it', () => {
		expect(toApi('text', '  Hello ')).toBe('Hello');
		expect(toInput('text', 'Hello')).toBe('Hello');
	});

	it('removes a value when the field is empty', () => {
		expect(toApi('text', '  ')).toBeNull();
		expect(toApi('amount', { amount: '', currency: 'EUR' })).toBeNull();
		expect(toApi('boolean', '')).toBeNull();
	});

	it('writes a number with a decimal point', () => {
		expect(toApi('number', '12,5')).toBe('12.5');
	});

	it('reads and writes yes/no', () => {
		expect(toInput('boolean', false)).toBe('false');
		expect(toApi('boolean', 'false')).toBe(false);
		expect(toApi('boolean', 'true')).toBe(true);
	});

	it('writes money with an upper-case currency', () => {
		expect(toApi('amount', { amount: '84,20', currency: 'eur' })).toEqual({
			amount: '84.20',
			currency: 'EUR'
		});
		expect(emptyValue('amount')).toEqual({ amount: '', currency: 'EUR' });
	});

	it('compares values', () => {
		expect(sameValue({ amount: '1', currency: 'EUR' }, { amount: '1', currency: 'EUR' })).toBe(
			true
		);
		expect(sameValue(undefined, null)).toBe(true);
		expect(sameValue('a', 'b')).toBe(false);
	});
});
