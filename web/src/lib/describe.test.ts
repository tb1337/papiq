import { describe, expect, it } from 'vitest';
import { describeValue, fieldLabel } from './describe.ts';
import type { Lookup } from './masterdata.svelte.ts';

const lookup = {
	contacts: [{ id: 'c1', name: 'ACME', created_at: '' }],
	documentTypes: [],
	tags: [{ id: 't1', name: 'Tax', created_at: '' }],
	attributes: [{ id: 'a1', name: 'Due', data_type: 'date' }],
	drawers: [],
	users: [{ id: 'u1', username: 'alice' }]
} as unknown as Lookup;

describe('describing changes', () => {
	it('names ids', () => {
		expect(describeValue('c1', lookup)).toBe('ACME');
		expect(describeValue(['t1', 'zz'], lookup)).toBe('Tax, zz');
		expect(describeValue('u1', lookup)).toBe('alice');
	});

	it('shows plain values', () => {
		expect(describeValue(true, lookup)).not.toBe('');
		expect(describeValue('hello', lookup)).toBe('hello');
		expect(describeValue({ amount: '1.5', currency: 'EUR' }, lookup)).toContain('1');
	});

	it('names fields', () => {
		expect(fieldLabel('attribute:a1', lookup)).toBe('Due');
		expect(fieldLabel('attribute:zz', lookup)).toBe('attribute:zz');
	});
});
