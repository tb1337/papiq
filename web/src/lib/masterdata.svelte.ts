/**
 * Contacts, document types, tags, attributes and drawers, as the pickers and filters need them.
 * They are few per household; loaded in full, once per page that needs them.
 */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { components } from '#lib/api/schema.ts';

export type MasterData = components['schemas']['MasterDataOut'];
export type Attribute = components['schemas']['AttributeOut'];
export type Drawer = components['schemas']['DrawerOut'];
export type UserName = components['schemas']['UserOut'];

export interface Lookup {
	contacts: MasterData[];
	documentTypes: MasterData[];
	tags: MasterData[];
	attributes: Attribute[];
	drawers: Drawer[];
	users: UserName[];
}

export async function loadLookup(): Promise<Lookup> {
	const [contacts, documentTypes, tags, attributes, drawers, users] = await Promise.all([
		unwrap(api.GET('/api/v1/contacts')),
		unwrap(api.GET('/api/v1/document-types')),
		unwrap(api.GET('/api/v1/tags')),
		unwrap(api.GET('/api/v1/attributes')),
		unwrap(api.GET('/api/v1/drawers')),
		unwrap(api.GET('/api/v1/users'))
	]);
	return { contacts, documentTypes, tags, attributes, drawers, users };
}

/** Name by id for a list of master data. */
export function names(list: readonly { id: string; name: string }[]): Map<string, string> {
	// eslint-disable-next-line svelte/prefer-svelte-reactivity
	return new Map(list.map((entry) => [entry.id, entry.name]));
}

/** The attributes that apply to a document type: global ones and those listed for it. */
export function attributesFor(
	attributes: readonly Attribute[],
	documentTypeId: string | null
): Attribute[] {
	return attributes.filter(
		(attribute) =>
			attribute.document_type_ids === null ||
			(documentTypeId !== null && attribute.document_type_ids.includes(documentTypeId))
	);
}
