/** Create, change and delete for the three simple kinds of master data. */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { MasterData } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';

export type SimpleKind = 'contacts' | 'document-types' | 'tags';

/** A contact's aliases or a document type's description; tags have neither. */
export type Extra = 'aliases' | 'description' | null;

/** One item as listed: contacts carry aliases, document types a description. */
export type Item = MasterData & { aliases?: string[]; description?: string | null };

/** What a dialog edits: the name and, by kind, aliases or a description. */
export interface Values {
	name: string;
	aliases: string[];
	description: string | null;
}

export interface KindOps {
	title: () => string;
	add: () => string;
	extra: Extra;
	list(): Promise<Item[]>;
	create(values: Values): Promise<unknown>;
	change(id: string, values: Values): Promise<unknown>;
	remove(id: string): Promise<unknown>;
}

const path = (id: string) => ({ params: { path: { id } } });

export const KINDS: Record<SimpleKind, KindOps> = {
	contacts: {
		title: m.master_contacts,
		add: m.master_add_contact,
		extra: 'aliases',
		list: () => unwrap(api.GET('/api/v1/contacts')),
		create: ({ name, aliases }) =>
			unwrap(api.POST('/api/v1/contacts', { body: { name, aliases } })),
		change: (id, { name, aliases }) =>
			unwrap(api.PATCH('/api/v1/contacts/{id}', { ...path(id), body: { name, aliases } })),
		remove: (id) => unwrap(api.DELETE('/api/v1/contacts/{id}', path(id)))
	},
	'document-types': {
		title: m.master_types,
		add: m.master_add_type,
		extra: 'description',
		list: () => unwrap(api.GET('/api/v1/document-types')),
		create: ({ name, description }) =>
			unwrap(api.POST('/api/v1/document-types', { body: { name, description } })),
		change: (id, { name, description }) =>
			unwrap(
				api.PATCH('/api/v1/document-types/{id}', { ...path(id), body: { name, description } })
			),
		remove: (id) => unwrap(api.DELETE('/api/v1/document-types/{id}', path(id)))
	},
	tags: {
		title: m.master_tags,
		add: m.master_add_tag,
		extra: null,
		list: () => unwrap(api.GET('/api/v1/tags')),
		create: ({ name }) => unwrap(api.POST('/api/v1/tags', { body: { name } })),
		change: (id, { name }) =>
			unwrap(api.PATCH('/api/v1/tags/{id}', { ...path(id), body: { name } })),
		remove: (id) => unwrap(api.DELETE('/api/v1/tags/{id}', path(id)))
	}
};

/** One alias per line; empty lines dropped. */
export function parseAliases(text: string): string[] {
	return text
		.split('\n')
		.map((line) => line.trim())
		.filter((line) => line !== '');
}
