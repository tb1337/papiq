import { describe, expect, it } from 'vitest';
import { buildConfirm, initialValue } from './review.ts';
import type { components } from '#lib/api/schema.ts';

type Document = components['schemas']['DocumentDetails'];
type Check = components['schemas']['FieldCheckOut'];

const document = {
	drawer_id: 'd1',
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

	it('decides every open field, empty meaning no value', () => {
		const confirm = buildConfirm(
			[check('contact', null), check('document_date', null), check('field:a1', null)],
			{ contact: '', document_date: '2026-03-31', 'field:a1': '' },
			document,
			fields,
			'd1'
		);
		expect(confirm.changes).toEqual({
			contact_id: null,
			document_date: '2026-03-31',
			fields: { a1: null }
		});
		expect(confirm.resume_at).toBe('apply_rules');
		expect(confirm.drawer_id).toBeUndefined();
	});

	it('extracts fields again after a corrected type, and moves on request', () => {
		const confirm = buildConfirm(
			[check('document_type', 'x'), check('drawer', null)],
			{ document_type: 'type-new' },
			document,
			fields,
			'd2'
		);
		expect(confirm.resume_at).toBe('extract_fields');
		expect(confirm.drawer_id).toBe('d2');
		expect(confirm.changes).toEqual({ document_type_id: 'type-new' });
	});
});
