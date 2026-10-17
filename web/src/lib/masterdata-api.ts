/** Create, change and delete for the simple kinds of master data: contacts, document types
 * and the tags of each scope (document tags, contact groups, type groups, topics). */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { IdentifierIn, IdentifierOut } from '#lib/identifiers.ts';
import type { Contact, MasterData, Tag, TagScope } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';

export type TagKind = 'tags' | 'contact-groups' | 'type-groups' | 'topics';
export type SimpleKind = 'contacts' | 'document-types' | TagKind;

/** The scope of the tags of each tag kind. */
export const TAG_KINDS: Record<TagKind, TagScope> = {
	tags: 'document',
	'contact-groups': 'contact',
	'type-groups': 'document_type',
	topics: 'topic'
};

export function isTagKind(kind: string): kind is TagKind {
	return kind in TAG_KINDS;
}

/** The tag kind of `scope`. */
export function kindOfScope(scope: TagScope): TagKind {
	return (Object.keys(TAG_KINDS) as TagKind[]).find((kind) => TAG_KINDS[kind] === scope) ?? 'tags';
}

/** A contact's aliases and groups, a document type's description and groups, a tag's place
 * in its tree. */
export type Extra = 'aliases' | 'description' | 'tag';

/** One item as listed: contacts carry aliases, identifiers, groups, topics and their logo,
 * document types a description, groups and an icon, tags their scope, parent, path, icon and
 * colour. */
export type Item = MasterData & {
	aliases?: string[];
	identifiers?: IdentifierOut[];
	description?: string | null;
	title_pattern?: string | null;
	group_ids?: string[];
	topic_ids?: string[];
	scope?: Tag['scope'];
	parent_id?: string | null;
	path?: string[];
	assignment?: Tag['assignment'];
	shown?: boolean;
	icon?: string | null;
	color?: string | null;
	logo?: Contact['logo'];
	logo_failure?: Contact['logo_failure'];
	/** With `ListOptions.counts`: how many documents (in the scope) have it. */
	document_count?: number | null;
	/** With `ListOptions.counts`: the contacts of a contact group or topic, the document types
	 * of a type group. */
	contact_count?: number | null;
	document_type_count?: number | null;
};

/** A list may count the documents of each item, within the caller's reach or of all users. */
export interface ListOptions {
	counts?: boolean;
	allUsers?: boolean;
}

const listQuery = (options: ListOptions = {}) => ({
	params: {
		query: {
			document_counts: options.counts || undefined,
			all_users: options.allUsers || undefined
		}
	}
});

/** What a dialog edits: the name and, by kind, aliases and identifiers or a description. */
export interface Values {
	name: string;
	aliases: string[];
	identifiers: IdentifierIn[];
	rejected_identifiers: IdentifierIn[];
	description: string | null;
	title_pattern: string | null;
	/** A document type's fields, global ones aside; null where they do not change. */
	fields: { id: string; required: boolean }[] | null;
	/** The groups of a contact or a document type. */
	group_ids: string[];
	/** The topics a contact enables. */
	topic_ids: string[];
	/** A tag's parent, how it is set, whether documents show it. */
	parent_id: string | null;
	assignment: Tag['assignment'];
	shown: boolean;
	/** The icon of a tag or a document type, the colour of a tag; null: none. */
	icon: string | null;
	color: string | null;
	/** A tag's scope (chosen for a new one); null for the other kinds. */
	scope: TagScope | null;
}

export interface KindOps {
	title: () => string;
	add: () => string;
	extra: Extra;
	/** The parameter of the document view that filters by an item. */
	filter: 'contact' | 'type' | 'tag' | 'contact_group' | 'type_group' | 'topic';
	list(options?: ListOptions): Promise<Item[]>;
	get(id: string): Promise<Item>;
	create(values: Values): Promise<Item>;
	change(id: string, values: Values): Promise<unknown>;
	remove(id: string): Promise<unknown>;
}

const path = (id: string) => ({ params: { path: { id } } });

export const KINDS: Record<SimpleKind, KindOps> = {
	contacts: {
		title: m.master_contacts,
		add: m.master_add_contact,
		extra: 'aliases',
		filter: 'contact',
		list: (options) => unwrap(api.GET('/api/v1/contacts', listQuery(options))),
		get: (id) => unwrap(api.GET('/api/v1/contacts/{id}', path(id))),
		create: ({ name, aliases, identifiers, group_ids, topic_ids }) =>
			unwrap(
				api.POST('/api/v1/contacts', {
					body: { name, aliases, identifiers, group_ids, topic_ids }
				})
			),
		change: (id, { name, aliases, identifiers, rejected_identifiers, group_ids, topic_ids }) =>
			unwrap(
				api.PATCH('/api/v1/contacts/{id}', {
					...path(id),
					body: { name, aliases, identifiers, rejected_identifiers, group_ids, topic_ids }
				})
			),
		remove: (id) => unwrap(api.DELETE('/api/v1/contacts/{id}', path(id)))
	},
	'document-types': {
		title: m.master_types,
		add: m.master_add_type,
		extra: 'description',
		filter: 'type',
		list: (options) => unwrap(api.GET('/api/v1/document-types', listQuery(options))),
		get: (id) => unwrap(api.GET('/api/v1/document-types/{id}', path(id))),
		create: ({ name, description, title_pattern, group_ids, icon }) =>
			unwrap(
				api.POST('/api/v1/document-types', {
					body: { name, description, title_pattern, group_ids, icon }
				})
			),
		change: (id, { name, description, title_pattern, group_ids, icon }) =>
			unwrap(
				api.PATCH('/api/v1/document-types/{id}', {
					...path(id),
					body: { name, description, title_pattern, group_ids, icon }
				})
			),
		remove: (id) => unwrap(api.DELETE('/api/v1/document-types/{id}', path(id)))
	},
	tags: tagOps('tags', m.master_document_tags, m.master_add_document_tag, 'tag'),
	'contact-groups': tagOps(
		'contact-groups',
		m.master_contact_groups,
		m.master_add_contact_group,
		'contact_group'
	),
	'type-groups': tagOps('type-groups', m.master_type_groups, m.master_add_type_group, 'type_group'),
	topics: tagOps('topics', m.master_topics, m.master_add_topic, 'topic')
};

/** The tags of one scope: groups are always set by hand, so their assignment is not sent. */
function tagOps(
	kind: TagKind,
	title: () => string,
	add: () => string,
	filter: KindOps['filter']
): KindOps {
	const scope = TAG_KINDS[kind];
	const assigned = (assignment: Tag['assignment']) =>
		scope === 'document' || scope === 'topic' ? { assignment } : {};
	return {
		title,
		add,
		extra: 'tag',
		filter,
		list: (options) =>
			unwrap(
				api.GET('/api/v1/tags', {
					params: { query: { ...listQuery(options).params.query, scope } }
				})
			),
		get: (id) => unwrap(api.GET('/api/v1/tags/{id}', path(id))),
		create: ({ name, parent_id, assignment, shown, icon, color }) =>
			unwrap(
				api.POST('/api/v1/tags', {
					body: { name, scope, parent_id, shown, icon, color, ...assigned(assignment) }
				})
			),
		change: (id, { name, parent_id, assignment, shown, icon, color }) =>
			unwrap(
				api.PATCH('/api/v1/tags/{id}', {
					...path(id),
					body: { name, parent_id, shown, icon, color, ...assigned(assignment) }
				})
			),
		remove: (id) => unwrap(api.DELETE('/api/v1/tags/{id}', path(id)))
	};
}

/** One alias per line; empty lines dropped. */
export function parseAliases(text: string): string[] {
	return text
		.split('\n')
		.map((line) => line.trim())
		.filter((line) => line !== '');
}
