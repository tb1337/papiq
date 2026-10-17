/** Texts of the rule builder: fields, operators, actions, problems; a rule as sentences. */
import { describeValue } from '#lib/describe.ts';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import type {
	ActionNode,
	ActionType,
	ApiAction,
	FieldType,
	Channel,
	ConditionField,
	ConditionNode,
	Operator,
	Problem,
	Trigger
} from '#lib/rules/model.ts';
import { isTagField } from '#lib/rules/model.ts';
import { pathLabel } from '#lib/tags.ts';

const CONDITION_FIELDS: Record<Exclude<ConditionField, 'field'>, () => string> = {
	contact: m.field_contact,
	contact_group: m.rule_field_contact_group,
	document_type: m.field_document_type,
	document_type_group: m.rule_field_document_type_group,
	topic: m.field_topic,
	tags: m.field_tags,
	channel: m.rule_field_channel,
	text: m.rule_field_text,
	document_date: m.field_date
};

export function fieldLabel(field: ConditionField): string {
	return field === 'field' ? m.rule_field_field() : CONDITION_FIELDS[field]();
}

export const TRIGGER_LABELS: Record<Trigger, () => string> = {
	ingest: m.rule_trigger_ingest,
	change: m.rule_trigger_change
};

export const CHANNEL_LABELS: Record<Channel, () => string> = {
	web: m.rule_channel_web,
	api: m.rule_channel_api,
	migration: m.rule_channel_migration
};

export const ACTION_LABELS: Record<ActionType, () => string> = {
	set_space: m.rule_action_set_space,
	set_contact: m.rule_action_set_contact,
	set_document_type: m.rule_action_set_document_type,
	set_topic: m.rule_action_set_topic,
	set_title: m.rule_action_set_title,
	add_tags: m.rule_action_add_tags,
	remove_tags: m.rule_action_remove_tags,
	set_field: m.rule_action_set_field,
	force_review: m.rule_action_force_review
};

const OPERATORS: Record<Operator, () => string> = {
	is: m.rule_op_is,
	in: m.rule_op_in,
	contains: m.rule_op_contains,
	matches: m.rule_op_matches,
	gt: m.rule_op_number_gt,
	lt: m.rule_op_number_lt,
	present: m.rule_op_present,
	missing: m.rule_op_missing,
	overlaps: m.rule_op_overlaps
};
const TAG_OPERATORS: Partial<Record<Operator, () => string>> = {
	contains: m.rule_op_tags_contains,
	in: m.rule_op_tags_in,
	present: m.rule_op_tags_present,
	missing: m.rule_op_tags_missing
};
const DATE_OPERATORS: Partial<Record<Operator, () => string>> = {
	is: m.rule_op_date_is,
	gt: m.rule_op_date_gt,
	lt: m.rule_op_date_lt
};
const NUMBER_OPERATORS: Partial<Record<Operator, () => string>> = {
	is: m.rule_op_number_is
};
const PERIOD_OPERATORS: Partial<Record<Operator, () => string>> = {
	gt: m.rule_op_period_gt,
	lt: m.rule_op_period_lt
};

/** The operator in the words of the field: tags "has", dates "after", numbers "greater". */
export function operatorLabel(
	field: ConditionField,
	op: Operator,
	type: FieldType | null = null
): string {
	let special: Partial<Record<Operator, () => string>> = {};
	if (isTagField(field)) special = TAG_OPERATORS;
	else if (field === 'document_date' || type === 'date') special = DATE_OPERATORS;
	else if (type === 'number' || type === 'amount') special = NUMBER_OPERATORS;
	else if (type === 'period') special = PERIOD_OPERATORS;
	return (special[op] ?? OPERATORS[op])();
}

const PROBLEMS: Record<Problem['code'], (params: Record<string, string | number>) => string> = {
	name: () => m.rule_error_name(),
	name_long: (params) => m.rule_error_text_long({ max: params.max }),
	priority: () => m.rule_error_priority(),
	triggers: () => m.rule_error_triggers(),
	value: () => m.rule_error_value(),
	choose: () => m.rule_error_choose(),
	list: () => m.rule_error_list(),
	list_long: (params) => m.rule_error_list_long({ max: params.max }),
	text_long: (params) => m.rule_error_text_long({ max: params.max }),
	date: () => m.rule_error_date(),
	number: () => m.rule_error_number(),
	amount: () => m.rule_error_amount(),
	period: () => m.rule_error_period(),
	field: () => m.rule_error_field(),
	operator: () => m.rule_error_operator(),
	group_empty: () => m.rule_error_group_empty(),
	conditions_many: (params) => m.rule_error_conditions_many({ max: params.max }),
	depth: (params) => m.rule_error_depth({ max: params.max }),
	actions_none: () => m.rule_error_actions_none(),
	actions_many: (params) => m.rule_error_actions_many({ max: params.max }),
	twice: () => m.rule_error_twice(),
	scope: () => m.rule_error_scope()
};

export function problemText(problem: Problem): string {
	return PROBLEMS[problem.code](problem.params ?? {});
}

/** A name for an id, or "unknown" for something deleted or not visible. */
export function nameOf(id: string, list: readonly { id: string; name: string }[]): string {
	return list.find((entry) => entry.id === id)?.name ?? m.rule_unknown();
}

function fieldOf(id: string | null, lookup: Lookup | null) {
	return lookup?.fields.find((field) => field.id === id) ?? null;
}

/** A condition as the user reads it: "Contact is Telekom". */
export function describeCondition(condition: ConditionNode, lookup: Lookup | null): string {
	const field = fieldOf(condition.fieldId, lookup);
	const subject =
		condition.field === 'field' ? (field?.name ?? m.rule_unknown()) : fieldLabel(condition.field);
	const op = operatorLabel(condition.field, condition.op, field?.data_type ?? null);
	if (condition.value === null) return `${subject} ${op}`;
	const values = Array.isArray(condition.value) ? condition.value : [condition.value];
	const text = values.map((value) => describeConditionValue(condition, value, lookup)).join(', ');
	const quoted =
		condition.field === 'text' || condition.op === 'contains' || condition.op === 'matches';
	const shown = quoted && !isTagField(condition.field) ? m.rule_quoted({ text }) : text;
	const caseNote = condition.caseSensitive ? ` (${m.rule_case_sensitive()})` : '';
	return `${subject} ${op} ${shown}${caseNote}`;
}

function describeConditionValue(
	condition: ConditionNode,
	value: unknown,
	lookup: Lookup | null
): string {
	if (typeof value === 'string') {
		if (condition.field === 'contact') return nameOf(value, lookup?.contacts ?? []);
		if (condition.field === 'document_type') return nameOf(value, lookup?.documentTypes ?? []);
		if (isTagField(condition.field) || condition.field === 'topic') return tagName(value, lookup);
		if (condition.field === 'channel') return CHANNEL_LABELS[value as Channel]?.() ?? value;
		if (condition.field === 'text') return value;
		if (condition.op === 'contains' || condition.op === 'matches') return value;
	}
	return describeValue(value, null);
}

/** The template of an action (a title, a text field's value, a review's reason) and what
 * stands before it ("Set field: Note = "); null for an action without one. */
export function actionTemplate(
	action: ActionNode | ApiAction,
	lookup: Lookup | null
): { lead: string; template: string } | null {
	const label = ACTION_LABELS[action.type]();
	switch (action.type) {
		case 'set_title':
			return { lead: `${label}: `, template: action.template };
		case 'force_review':
			return { lead: `${label}: `, template: action.reason };
		case 'set_field': {
			const field = fieldOf(action.field_id, lookup);
			if (field?.data_type !== 'text' || typeof action.value !== 'string') return null;
			return { lead: `${label}: ${field.name} = `, template: action.value };
		}
	}
	return null;
}

/** A tag by its path ("Insurance › Health"); unknown ones as such. */
function tagName(id: string, lookup: Lookup | null): string {
	const tag = lookup?.tags.find((entry) => entry.id === id);
	return tag ? pathLabel(tag) : nameOf(id, []);
}

/** An action as the user reads it: "Add tags: Tax, Bills". */
export function describeAction(action: ActionNode | ApiAction, lookup: Lookup | null): string {
	const label = ACTION_LABELS[action.type]();
	switch (action.type) {
		case 'set_space':
			return `${label}: ${nameOf(action.space_id, lookup?.spaces ?? [])}`;
		case 'set_contact':
			return `${label}: ${nameOf(action.contact_id, lookup?.contacts ?? [])}`;
		case 'set_document_type':
			return `${label}: ${nameOf(action.document_type_id, lookup?.documentTypes ?? [])}`;
		case 'set_topic':
			return `${label}: ${tagName(action.topic_id, lookup)}`;
		case 'set_title':
			return `${label}: ${m.rule_quoted({ text: action.template })}`;
		case 'add_tags':
		case 'remove_tags':
			return `${label}: ${action.tag_ids.map((id) => tagName(id, lookup)).join(', ')}`;
		case 'set_field': {
			const field = fieldOf(action.field_id, lookup);
			return `${label}: ${field?.name ?? m.rule_unknown()} = ${describeValue(action.value, null)}`;
		}
		case 'force_review':
			return `${label}: ${m.rule_quoted({ text: action.reason })}`;
	}
}
