/** Texts of the rule builder: fields, operators, actions, problems; a rule as sentences. */
import { describeValue } from '#lib/describe.ts';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import type {
	ActionNode,
	ActionType,
	ApiAction,
	AttributeType,
	Channel,
	ConditionField,
	ConditionNode,
	Operator,
	Problem,
	Trigger
} from '#lib/rules/model.ts';

const FIELDS: Record<Exclude<ConditionField, 'attribute'>, () => string> = {
	contact: m.field_contact,
	document_type: m.field_document_type,
	tags: m.field_tags,
	channel: m.rule_field_channel,
	text: m.rule_field_text,
	document_date: m.field_date
};

export function fieldLabel(field: ConditionField): string {
	return field === 'attribute' ? m.rule_field_attribute() : FIELDS[field]();
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
	set_drawer: m.rule_action_set_drawer,
	set_contact: m.rule_action_set_contact,
	set_document_type: m.rule_action_set_document_type,
	set_title: m.rule_action_set_title,
	add_tags: m.rule_action_add_tags,
	remove_tags: m.rule_action_remove_tags,
	set_attribute: m.rule_action_set_attribute,
	force_review: m.rule_action_force_review
};

export const PLACEHOLDER_LABELS: Record<string, () => string> = {
	contact: m.field_contact,
	document_type: m.field_document_type,
	document_date: m.field_date,
	filename: m.rule_placeholder_filename
};

const OPERATORS: Record<Operator, () => string> = {
	is: m.rule_op_is,
	in: m.rule_op_in,
	contains: m.rule_op_contains,
	matches: m.rule_op_matches,
	gt: m.rule_op_number_gt,
	lt: m.rule_op_number_lt,
	present: m.rule_op_present,
	missing: m.rule_op_missing
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

/** The operator in the words of the field: tags "has", dates "after", numbers "greater". */
export function operatorLabel(
	field: ConditionField,
	op: Operator,
	type: AttributeType | null = null
): string {
	let special: Partial<Record<Operator, () => string>> = {};
	if (field === 'tags') special = TAG_OPERATORS;
	else if (field === 'document_date' || type === 'date') special = DATE_OPERATORS;
	else if (type === 'number' || type === 'amount') special = NUMBER_OPERATORS;
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
	attribute: () => m.rule_error_attribute(),
	operator: () => m.rule_error_operator(),
	group_empty: () => m.rule_error_group_empty(),
	conditions_many: (params) => m.rule_error_conditions_many({ max: params.max }),
	depth: (params) => m.rule_error_depth({ max: params.max }),
	actions_none: () => m.rule_error_actions_none(),
	actions_many: (params) => m.rule_error_actions_many({ max: params.max }),
	twice: () => m.rule_error_twice(),
	scope: () => m.rule_error_scope(),
	placeholder: (params) => m.rule_error_placeholder({ name: params.name })
};

export function problemText(problem: Problem): string {
	return PROBLEMS[problem.code](problem.params ?? {});
}

/** A name for an id, or "unknown" for something deleted or not visible. */
export function nameOf(id: string, list: readonly { id: string; name: string }[]): string {
	return list.find((entry) => entry.id === id)?.name ?? m.rule_unknown();
}

function attributeOf(id: string | null, lookup: Lookup | null) {
	return lookup?.attributes.find((attribute) => attribute.id === id) ?? null;
}

/** A condition as the user reads it: "Contact is Telekom". */
export function describeCondition(condition: ConditionNode, lookup: Lookup | null): string {
	const attribute = attributeOf(condition.attributeId, lookup);
	const subject =
		condition.field === 'attribute'
			? (attribute?.name ?? m.rule_unknown())
			: fieldLabel(condition.field);
	const op = operatorLabel(condition.field, condition.op, attribute?.data_type ?? null);
	if (condition.value === null) return `${subject} ${op}`;
	const values = Array.isArray(condition.value) ? condition.value : [condition.value];
	const text = values.map((value) => describeConditionValue(condition, value, lookup)).join(', ');
	const quoted =
		condition.field === 'text' || condition.op === 'contains' || condition.op === 'matches';
	const shown = quoted && condition.field !== 'tags' ? m.rule_quoted({ text }) : text;
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
		if (condition.field === 'tags') return nameOf(value, lookup?.tags ?? []);
		if (condition.field === 'channel') return CHANNEL_LABELS[value as Channel]?.() ?? value;
		if (condition.field === 'text') return value;
		if (condition.op === 'contains' || condition.op === 'matches') return value;
	}
	return describeValue(value, null);
}

/** An action as the user reads it: "Add tags: Tax, Bills". */
export function describeAction(action: ActionNode | ApiAction, lookup: Lookup | null): string {
	const label = ACTION_LABELS[action.type]();
	switch (action.type) {
		case 'set_drawer':
			return `${label}: ${nameOf(action.drawer_id, lookup?.drawers ?? [])}`;
		case 'set_contact':
			return `${label}: ${nameOf(action.contact_id, lookup?.contacts ?? [])}`;
		case 'set_document_type':
			return `${label}: ${nameOf(action.document_type_id, lookup?.documentTypes ?? [])}`;
		case 'set_title':
			return `${label}: ${m.rule_quoted({ text: action.template })}`;
		case 'add_tags':
		case 'remove_tags':
			return `${label}: ${action.tag_ids.map((id) => nameOf(id, lookup?.tags ?? [])).join(', ')}`;
		case 'set_attribute': {
			const attribute = attributeOf(action.attribute_id, lookup);
			return `${label}: ${attribute?.name ?? m.rule_unknown()} = ${describeValue(action.value, null)}`;
		}
		case 'force_review':
			return `${label}: ${m.rule_quoted({ text: action.reason })}`;
	}
}
