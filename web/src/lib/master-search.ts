/**
 * The search of the master data lists, in this browser (a household's master data are few):
 * every word of the query must stand in what a row is about, regardless of case. A contact is
 * found by its name, aliases and identifiers (an IBAN with or without spaces), a document type
 * by its name and description, a field by its name, key and description.
 */
import type { Contact, DocumentType, FieldDefinition } from '#lib/masterdata.svelte.ts';

function words(query: string): string[] {
	return query
		.toLocaleLowerCase()
		.split(/\s+/)
		.filter((word) => word !== '');
}

function found(query: string, texts: readonly (string | null | undefined)[]): boolean {
	const haystack = texts
		.filter((text): text is string => !!text)
		.join('\n')
		.toLocaleLowerCase();
	return words(query).every((word) => haystack.includes(word));
}

export function contactMatches(
	contact: Pick<Contact, 'name'> & Partial<Pick<Contact, 'aliases' | 'identifiers'>>,
	query: string
): boolean {
	const values = (contact.identifiers ?? [])
		.filter((entry) => entry.source !== 'rejected')
		.flatMap((entry) => [entry.value, entry.value.replace(/\s+/g, '')]);
	return found(query, [contact.name, ...(contact.aliases ?? []), ...values]);
}

export function typeMatches(
	type: Pick<DocumentType, 'name'> & Partial<Pick<DocumentType, 'description'>>,
	query: string
): boolean {
	return found(query, [type.name, type.description]);
}

export function fieldMatches(
	field: Pick<FieldDefinition, 'name' | 'key' | 'description'>,
	query: string
): boolean {
	return found(query, [field.name, field.key, `field.${field.key}`, field.description]);
}
