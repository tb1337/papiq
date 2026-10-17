import type { FieldDefinition } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';

/** The values a template sees, as the server names them (`field.<key>` aside). */
export const TEMPLATE_NAMES = [
	'title',
	'contact',
	'document_type',
	'topic',
	'document_date',
	'added',
	'tags',
	'contact_groups',
	'document_type_groups',
	'space',
	'owner',
	'filename',
	'original_filename',
	'channel',
	'pages'
] as const;
export type TemplateName = (typeof TEMPLATE_NAMES)[number];

export const TEMPLATE_NAME_LABELS: Record<TemplateName, () => string> = {
	title: m.field_title,
	contact: m.field_contact,
	document_type: m.field_document_type,
	topic: m.field_topic,
	document_date: m.field_date,
	added: m.template_value_added,
	tags: m.field_tags,
	contact_groups: m.template_value_contact_groups,
	document_type_groups: m.template_value_document_type_groups,
	space: m.field_space,
	owner: m.template_value_owner,
	filename: m.template_value_filename,
	original_filename: m.template_value_original_filename,
	channel: m.template_value_channel,
	pages: m.template_value_pages
};

/** The lists among the values: a template joins them (`{{ tags | join: ', ' }}`). */
export const LIST_NAMES: ReadonlySet<string> = new Set([
	'tags',
	'contact_groups',
	'document_type_groups'
]);

/** The fields a template may use: those of `documentType` (undefined: every field, as in a
 * rule; null: a type not saved yet, so the fields for every type). */
export function usableFields(
	fields: readonly FieldDefinition[],
	documentType: string | null | undefined
): FieldDefinition[] {
	return fields.filter(
		(field) =>
			documentType === undefined ||
			field.document_type_ids === null ||
			(documentType !== null && field.document_type_ids.includes(documentType))
	);
}
