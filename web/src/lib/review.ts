/**
 * The decisions of a review: what each open field holds in the form. The confirmation itself
 * (`POST /documents/{id}/confirm`) is built by the document form.
 */
import type { components } from '#lib/api/schema.ts';
import { emptyValue, toInput, type ApiValue, type InputValue } from '#lib/fields.ts';
import type { FieldDefinition } from '#lib/masterdata.svelte.ts';

type Document = components['schemas']['DocumentDetails'];
type Check = components['schemas']['FieldCheckOut'];

/** What a form field holds: text (ids, dates), a list of ids (tags) or a field value. */
export type ReviewValue = string | string[] | InputValue;

/** Fields of the rules that need no input of their own: confirming keeps their value. */
export const RULE_FIELDS = ['space', 'title', 'review'] as const;

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
		case 'topic':
			return document.topic_id ?? (typeof check.suggestion === 'string' ? check.suggestion : '');
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
