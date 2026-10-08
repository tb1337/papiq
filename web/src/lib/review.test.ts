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
	attributes: {}
} as unknown as Document;

const check = (field: string, suggestion: unknown): Check =>
	({ field, outcome: 'uncertain', confidence: 0.4, suggestion }) as unknown as Check;

const attributes = [{ id: 'a1', name: 'Due', data_type: 'date', choices: [] }] as never;

describe('review decisions', () => {
	it('starts from the document, then the suggestion', () => {
		expect(initialValue(check('contact', 'c1'), document, attributes)).toBe('c1');
		expect(initialValue(check('document_type', 'type-new'), document, attributes)).toBe('type-old');
		expect(initialValue(check('tags', ['t1']), document, attributes)).toEqual(['t1']);
		expect(initialValue(check('attribute:a1', '2026-03-31'), document, attributes)).toBe(
			'2026-03-31'
		);
		expect(initialValue(check('document_date', null), document, attributes)).toBe('');
	});

	it('decides every open field, empty meaning no value', () => {
		const confirm = buildConfirm(
			[check('contact', null), check('document_date', null), check('attribute:a1', null)],
			{ contact: '', document_date: '2026-03-31', 'attribute:a1': '' },
			document,
			attributes,
			'd1'
		);
		expect(confirm.changes).toEqual({
			contact_id: null,
			document_date: '2026-03-31',
			attributes: { a1: null }
		});
		expect(confirm.resume_at).toBe('apply_rules');
		expect(confirm.drawer_id).toBeUndefined();
	});

	it('extracts attributes again after a corrected type, and moves on request', () => {
		const confirm = buildConfirm(
			[check('document_type', 'x'), check('drawer', null)],
			{ document_type: 'type-new' },
			document,
			attributes,
			'd2'
		);
		expect(confirm.resume_at).toBe('extract_attributes');
		expect(confirm.drawer_id).toBe('d2');
		expect(confirm.changes).toEqual({ document_type_id: 'type-new' });
	});
});
