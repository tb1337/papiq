/**
 * The decisions of a review: what each open field holds in the form, and the confirmation the API
 * wants from them (`POST /documents/{id}/confirm`).
 */
import type { components } from '#lib/api/schema.ts';
import { emptyValue, toApi, toInput, type ApiValue, type InputValue } from '#lib/fields.ts';
import type { FieldDefinition } from '#lib/masterdata.svelte.ts';

type Document = components['schemas']['DocumentDetails'];
type Check = components['schemas']['FieldCheckOut'];
type Patch = components['schemas']['DocumentPatch'];
export type Confirm = components['schemas']['ConfirmRequest'];

/** What a form field holds: text (ids, dates), a list of ids (tags) or a field value. */
export type ReviewValue = string | string[] | InputValue;

/** Fields of the rules that need no input of their own: confirming keeps their value. */
export const RULE_FIELDS = ['drawer', 'title', 'review'] as const;

export function isRuleField(field: string): boolean {
	return (RULE_FIELDS as readonly string[]).includes(field);
}

export function fieldId(field: string): string | null {
	return field.startsWith('field:') ? field.slice('field:'.length) : null;
}

/** The value a field starts with: what the document has, else the suggestion, else empty. */
export function initialValue(
	check: Check,
	document: Document,
	fields: readonly FieldDefinition[]
): ReviewValue {
	const id = fieldId(check.field);
	if (id !== null) {
		const type = fields.find((field) => field.id === id)?.data_type ?? 'text';
		const have = document.fields[id];
		const value = have ?? (check.suggestion as ApiValue | null | undefined);
		return value === undefined || value === null ? emptyValue(type) : toInput(type, value);
	}
	switch (check.field) {
		case 'contact':
			return document.contact_id ?? (typeof check.suggestion === 'string' ? check.suggestion : '');
		case 'document_type':
			return (
				document.document_type_id ?? (typeof check.suggestion === 'string' ? check.suggestion : '')
			);
		case 'document_date':
			return (
				document.document_date ?? (typeof check.suggestion === 'string' ? check.suggestion : '')
			);
		case 'tags':
			return document.tag_ids.length > 0
				? [...document.tag_ids]
				: Array.isArray(check.suggestion)
					? (check.suggestion as string[])
					: [];
		default:
			return '';
	}
}

/**
 * The confirmation for the decisions: every open field with its value (empty = no value), the
 * drawer if it changed, and the step to continue at: after a corrected document type the
 * fields of the new type are extracted again.
 */
export function buildConfirm(
	checks: readonly Check[],
	values: Readonly<Record<string, ReviewValue>>,
	document: Document,
	fields: readonly FieldDefinition[],
	drawerId: string
): Confirm {
	const changes: Patch = {};
	const fieldChanges: Record<string, ApiValue | null> = {};
	for (const check of checks) {
		if (isRuleField(check.field)) continue;
		const value = values[check.field];
		const id = fieldId(check.field);
		if (id !== null) {
			const type = fields.find((field) => field.id === id)?.data_type ?? 'text';
			fieldChanges[id] = toApi(type, value as InputValue);
		} else if (check.field === 'contact') changes.contact_id = (value as string) || null;
		else if (check.field === 'document_type') changes.document_type_id = (value as string) || null;
		else if (check.field === 'document_date') changes.document_date = (value as string) || null;
		else if (check.field === 'tags') changes.tag_ids = value as string[];
	}
	if (Object.keys(fieldChanges).length > 0) changes.fields = fieldChanges;
	const typeChanged =
		'document_type_id' in changes && changes.document_type_id !== document.document_type_id;
	return {
		changes,
		accept_suggestions: false,
		resume_at: typeChanged ? 'extract_fields' : 'apply_rules',
		...(drawerId && drawerId !== document.drawer_id ? { drawer_id: drawerId } : {})
	};
}
