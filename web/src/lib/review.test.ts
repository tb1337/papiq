import { describe, expect, it } from 'vitest';
import { initialValue } from './review.ts';
import type { components } from '#lib/api/schema.ts';

type Document = components['schemas']['DocumentDetails'];
type Check = components['schemas']['FieldCheckOut'];

const document = {
	space_id: 'd1',
	contact_id: null,
	document_type_id: 'type-old',
	document_date: null,
	tag_ids: [],
	fields: {}
} as unknown as Document;

const check = (field: string, suggestion: unknown): Check =>
	({ field, outcome: 'uncertain', confidence: 0.4, suggestion }) as unknown as Check;

const fields = [{ id: 'a1', name: 'Due', data_type: 'date', choices: [] }] as never;

describe('review decisions', () => {
	it('starts from the document, then the suggestion', () => {
		expect(initialValue(check('contact', 'c1'), document, fields)).toBe('c1');
		expect(initialValue(check('document_type', 'type-new'), document, fields)).toBe('type-old');
		expect(initialValue(check('tags', ['t1']), document, fields)).toEqual(['t1']);
		expect(initialValue(check('field:a1', '2026-03-31'), document, fields)).toBe('2026-03-31');
		expect(initialValue(check('document_date', null), document, fields)).toBe('');
	});
});
