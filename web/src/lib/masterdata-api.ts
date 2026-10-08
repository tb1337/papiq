/** Create, rename and delete for the three simple kinds of master data. */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { MasterData } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';

export type SimpleKind = 'contacts' | 'document-types' | 'tags';

export interface KindOps {
	title: () => string;
	add: () => string;
	list(): Promise<MasterData[]>;
	create(name: string): Promise<unknown>;
	rename(id: string, name: string): Promise<unknown>;
	remove(id: string): Promise<unknown>;
}

const path = (id: string) => ({ params: { path: { id } } });

export const KINDS: Record<SimpleKind, KindOps> = {
	contacts: {
		title: m.master_contacts,
		add: m.master_add_contact,
		list: () => unwrap(api.GET('/api/v1/contacts')),
		create: (name) => unwrap(api.POST('/api/v1/contacts', { body: { name } })),
		rename: (id, name) =>
			unwrap(api.PATCH('/api/v1/contacts/{id}', { ...path(id), body: { name } })),
		remove: (id) => unwrap(api.DELETE('/api/v1/contacts/{id}', path(id)))
	},
	'document-types': {
		title: m.master_types,
		add: m.master_add_type,
		list: () => unwrap(api.GET('/api/v1/document-types')),
		create: (name) => unwrap(api.POST('/api/v1/document-types', { body: { name } })),
		rename: (id, name) =>
			unwrap(api.PATCH('/api/v1/document-types/{id}', { ...path(id), body: { name } })),
		remove: (id) => unwrap(api.DELETE('/api/v1/document-types/{id}', path(id)))
	},
	tags: {
		title: m.master_tags,
		add: m.master_add_tag,
		list: () => unwrap(api.GET('/api/v1/tags')),
		create: (name) => unwrap(api.POST('/api/v1/tags', { body: { name } })),
		rename: (id, name) => unwrap(api.PATCH('/api/v1/tags/{id}', { ...path(id), body: { name } })),
		remove: (id) => unwrap(api.DELETE('/api/v1/tags/{id}', path(id)))
	}
};
