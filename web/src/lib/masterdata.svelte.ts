/**
 * Contacts, document types, tags, fields and drawers, as the pickers and filters need them.
 * They are few per household; loaded in full, once per page that needs them.
 */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { components } from '#lib/api/schema.ts';
import { m } from '#lib/paraglide/messages.js';

export type MasterData = components['schemas']['MasterDataOut'];
export type FieldDefinition = components['schemas']['FieldOut'];
export type Drawer = components['schemas']['DrawerOut'];
export type UserName = components['schemas']['UserOut'];

export interface Lookup {
	contacts: MasterData[];
	documentTypes: MasterData[];
	tags: MasterData[];
	fields: FieldDefinition[];
	drawers: Drawer[];
	users: UserName[];
}

export async function loadLookup(): Promise<Lookup> {
	const [contacts, documentTypes, tags, fields, drawers, users] = await Promise.all([
		unwrap(api.GET('/api/v1/contacts')),
		unwrap(api.GET('/api/v1/document-types')),
		unwrap(api.GET('/api/v1/tags')),
		unwrap(api.GET('/api/v1/fields')),
		unwrap(api.GET('/api/v1/drawers')),
		unwrap(api.GET('/api/v1/users'))
	]);
	return { contacts, documentTypes, tags, fields, drawers, users };
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

/** A drawer's name, with its owner's for another user's drawer (admins see every drawer). */
export function drawerLabel(
	drawer: Pick<Drawer, 'name' | 'owner_id'>,
	users: readonly UserName[],
	me: string | undefined
): string {
	if (drawer.owner_id === me) return drawer.name;
	const owner = users.find((user) => user.id === drawer.owner_id)?.username ?? drawer.owner_id;
	return m.drawer_of({ name: drawer.name, owner });
}
