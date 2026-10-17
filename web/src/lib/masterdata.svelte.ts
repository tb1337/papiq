/**
 * Contacts, document types, tags, fields and spaces, as the pickers and filters need them.
 * They are few per household; loaded in full, once per page that needs them.
 */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { components } from '#lib/api/schema.ts';
import { m } from '#lib/paraglide/messages.js';

export type Contact = components['schemas']['ContactOut'];
export type DocumentType = components['schemas']['DocumentTypeOut'];
/** What every kind of master data has. */
export type MasterData = Pick<Contact, 'id' | 'name' | 'created_at' | 'document_count'>;
export type Tag = components['schemas']['TagOut'];
export type TagScope = components['schemas']['TagScope'];
export type FieldDefinition = components['schemas']['FieldOut'];
export type Space = components['schemas']['SpaceOut'];
export type UserName = components['schemas']['UserOut'];

export interface Lookup {
	contacts: Contact[];
	documentTypes: DocumentType[];
	/** Of every scope: document tags, contact groups, type groups. */
	tags: Tag[];
	fields: FieldDefinition[];
	spaces: Space[];
	users: UserName[];
}

export async function loadLookup(): Promise<Lookup> {
	const [contacts, documentTypes, tags, fields, spaces, users] = await Promise.all([
		unwrap(api.GET('/api/v1/contacts')),
		unwrap(api.GET('/api/v1/document-types')),
		unwrap(api.GET('/api/v1/tags')),
		unwrap(api.GET('/api/v1/fields')),
		unwrap(api.GET('/api/v1/spaces')),
		unwrap(api.GET('/api/v1/users'))
	]);
	return { contacts, documentTypes, tags, fields, spaces, users };
}

/** Name by id for a list of master data. */
export function names(list: readonly { id: string; name: string }[]): Map<string, string> {
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	return new Map(list.map((entry) => [entry.id, entry.name]));
}

/** The fields that apply to a document type: global ones and those listed for it. */
export function fieldsFor(
	fields: readonly FieldDefinition[],
	documentTypeId: string | null
): FieldDefinition[] {
	return fields.filter(
		(field) =>
			field.document_type_ids === null ||
			(documentTypeId !== null && field.document_type_ids.includes(documentTypeId))
	);
}

/** A space's name, with its owner's for another user's space (admins see every space). */
export function spaceLabel(
	space: Pick<Space, 'name' | 'owner_id'>,
	users: readonly UserName[],
	me: string | undefined
): string {
	if (space.owner_id === me) return space.name;
	const owner = users.find((user) => user.id === space.owner_id)?.username ?? space.owner_id;
	return m.space_of({ name: space.name, owner });
}
