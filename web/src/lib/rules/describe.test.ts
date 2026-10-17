import { describe, expect, it } from 'vitest';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { actionTemplate, describeAction, describeCondition, operatorLabel } from './describe.ts';

function textField(id: string, name: string, data_type: string) {
	return {
		id,
		name,
		key: name.toLowerCase(),
		data_type,
		document_type_ids: null,
		choices: [],
		required: false,
		required_document_type_ids: [],
		description: null,
		created_at: '2026-10-13T12:00:00Z'
	};
}

const lookup = {
	contacts: [],
	documentTypes: [],
	tags: [],
	spaces: [],
	fields: [textField('f1', 'Notiz', 'text'), textField('f2', 'Betrag', 'amount')]
} as unknown as Lookup;

describe('the template of an action', () => {
	it('is the title, the review reason and a text field value', () => {
		expect(actionTemplate({ type: 'set_title', template: '{{ contact }}' }, lookup)).toEqual({
			lead: `${m.rule_action_set_title()}: `,
			template: '{{ contact }}'
		});
		expect(actionTemplate({ type: 'force_review', reason: 'Prüfen' }, lookup)?.template).toBe(
			'Prüfen'
		);
		expect(
			actionTemplate({ type: 'set_field', field_id: 'f1', value: '{{ title }}' }, lookup)
		).toEqual({ lead: `${m.rule_action_set_field()}: Notiz = `, template: '{{ title }}' });
	});

	it('is absent from other actions and other fields', () => {
		const amount = { amount: '1.00', currency: 'EUR' };
		expect(actionTemplate({ type: 'set_field', field_id: 'f2', value: amount }, lookup)).toBeNull();
		expect(actionTemplate({ type: 'add_tags', tag_ids: [] }, lookup)).toBeNull();
	});
});

describe('operators in the words of a period', () => {
	it('starts after, ends before, overlaps', () => {
		expect(operatorLabel('field', 'gt', 'period')).toBe(m.rule_op_period_gt());
		expect(operatorLabel('field', 'lt', 'period')).toBe(m.rule_op_period_lt());
		expect(operatorLabel('field', 'overlaps', 'period')).toBe(m.rule_op_overlaps());
		expect(operatorLabel('field', 'gt', 'date')).toBe(m.rule_op_date_gt());
	});
});

describe('conditions on groups', () => {
	const groups = {
		...lookup,
		tags: [
			{
				id: 'g1',
				name: 'Health insurer',
				path: ['Insurer', 'Health insurer'],
				parent_id: 'g0',
				scope: 'contact',
				assignment: 'manual',
				shown: false,
				created_at: '2026-10-16T12:00:00Z'
			}
		]
	} as unknown as Lookup;

	it('read as the group with its path, in the words of tags', () => {
		const condition = {
			kind: 'condition' as const,
			key: 'c1',
			field: 'contact_group' as const,
			op: 'contains' as const,
			value: 'g1',
			fieldId: null,
			caseSensitive: false
		};
		expect(describeCondition(condition, groups)).toBe(
			`${m.rule_field_contact_group()} ${m.rule_op_tags_contains()} Insurer › Health insurer`
		);
		expect(operatorLabel('document_type_group', 'in')).toBe(m.rule_op_tags_in());
	});
});

describe('the topic', () => {
	const topics = {
		...lookup,
		tags: [
			{
				id: 't1',
				name: 'Health',
				path: ['Insurance', 'Health'],
				parent_id: 't0',
				scope: 'topic',
				assignment: 'auto',
				shown: true,
				created_at: '2026-10-17T12:00:00Z'
			}
		]
	} as unknown as Lookup;

	it('reads as one value with its path, in the words of a contact', () => {
		const condition = {
			kind: 'condition' as const,
			key: 'c1',
			field: 'topic' as const,
			op: 'is' as const,
			value: 't1',
			fieldId: null,
			caseSensitive: false
		};
		expect(describeCondition(condition, topics)).toBe(
			`${m.field_topic()} ${m.rule_op_is()} Insurance › Health`
		);
		expect(describeAction({ type: 'set_topic', topic_id: 't1' }, topics)).toBe(
			`${m.rule_action_set_topic()}: Insurance › Health`
		);
	});
});
