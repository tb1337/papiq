<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { BASE } from '#lib/base.ts';
	import FieldDialog from '#lib/components/FieldDialog.svelte';
	import MasterDataDialog from '#lib/components/MasterDataDialog.svelte';
	import { describeError } from '#lib/errors.ts';
	import { logoFetching } from '#lib/logos.ts';
	import { type MasterDataRequest, masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import type { FieldDefinition, MasterData, Tag } from '#lib/masterdata.svelte.ts';
	import {
		isTagKind,
		type Item,
		KINDS,
		kindOfScope,
		type SimpleKind,
		TAG_KINDS,
		type Values
	} from '#lib/masterdata-api.ts';
	import { m } from '#lib/paraglide/messages.js';

	// The dialogs that add and change master data, for every page (`masterDataDialogs.open`):
	// contacts, document types, tags of each scope (a new one of any: its kind comes first) and
	// fields. A document type's dialog links the rule of its title pattern, unless it was opened
	// from that rule. A tag made with its details from a picker opens in a second dialog over
	// the first (`masterDataDialogs.createTag`).
	const request = $derived(masterDataDialogs.request);
	const anyTag = $derived(request?.kind === 'any-tag');
	const simple = $derived(
		request && request.kind !== 'fields' && request.kind !== 'any-tag'
			? (request.kind as SimpleKind)
			: null
	);

	let item = $state<Item | null>(null);
	let field = $state<FieldDefinition | null>(null);
	let types = $state<MasterData[]>([]);
	let ruleId = $state<string | null>(null);
	let fields = $state<FieldDefinition[] | null>(null);
	let tags = $state<Tag[]>([]);
	let ready = $state(false);
	let fetching = $state(false);

	// What the dialog shows, loaded where only an id is given. It returns before it is assigned:
	// the effect below must not read the state it sets.
	async function loaded(wanted: MasterDataRequest) {
		if (wanted.kind === 'fields') {
			const kinds = await unwrap(api.GET('/api/v1/document-types'));
			const definition =
				typeof wanted.item === 'string'
					? await unwrap(api.GET('/api/v1/fields/{id}', { params: { path: { id: wanted.item } } }))
					: (wanted.item as FieldDefinition | null);
			return { kinds, definition, entry: null, rule: null, all: null, every: [] };
		}
		if (wanted.kind === 'any-tag') {
			const every = await unwrap(api.GET('/api/v1/tags'));
			return { kinds: null, definition: null, entry: null, rule: null, all: null, every };
		}
		const entry =
			typeof wanted.item === 'string'
				? await KINDS[wanted.kind].get(wanted.item)
				: (wanted.item as Item | null);
		let rule: string | null = null;
		if (wanted.kind === 'document-types' && entry && !wanted.fromRule) {
			const rules = await unwrap(
				api.GET('/api/v1/rules', { params: { query: { scope: 'global' } } })
			);
			rule = rules.find((found) => found.managed_by_document_type_id === entry.id)?.id ?? null;
		}
		// A document type shows its fields.
		const all = wanted.kind === 'document-types' ? await unwrap(api.GET('/api/v1/fields')) : null;
		// A tag goes under another; contacts and types are in groups.
		const every = await unwrap(api.GET('/api/v1/tags'));
		// A contact's logo may be fetched from its website, if the server does that.
		if (wanted.kind === 'contacts') fetching = await logoFetching();
		return { kinds: null, definition: null, entry, rule, all, every };
	}

	$effect(() => {
		const wanted = request;
		ready = false;
		item = null;
		field = null;
		ruleId = null;
		fields = null;
		if (!wanted) return;
		let current = true;
		loaded(wanted).then(
			({ kinds, definition, entry, rule, all, every }) => {
				if (!current) return;
				if (kinds) types = kinds;
				tags = every;
				[item, field, ruleId, fields, ready] = [entry, definition, rule, all, true];
			},
			(failure) => {
				if (!current) return;
				toast.error(describeError(failure));
				masterDataDialogs.close();
			}
		);
		return () => {
			current = false;
		};
	});

	/** What an item of each kind is, for the bar of a phone. */
	const NOUN: Record<SimpleKind, () => string> = {
		contacts: m.master_contact,
		'document-types': m.master_type,
		tags: m.tag_kind_document,
		'contact-groups': m.tag_kind_contact,
		'type-groups': m.tag_kind_type,
		topics: m.tag_kind_topic
	};

	/** When an item of each kind can be deleted. */
	const DELETE_HINT: Record<SimpleKind, () => string> = {
		contacts: m.master_delete_confirm,
		'document-types': m.master_delete_confirm,
		tags: m.tag_delete_confirm,
		'contact-groups': m.contact_group_delete_confirm,
		'type-groups': m.type_group_delete_confirm,
		topics: m.topic_delete_confirm
	};

	// A picker made a tag: there at once, then in the order of the trees.
	async function created(tag: Tag) {
		tags = [...tags.filter((other) => other.id !== tag.id), tag];
		try {
			tags = await unwrap(api.GET('/api/v1/tags'));
		} catch (failure) {
			toast.error(describeError(failure));
		}
	}

	const nested = $derived(masterDataDialogs.nested);
	let nestedTags = $state<Tag[] | null>(null);

	$effect(() => {
		const wanted = nested;
		nestedTags = null;
		if (!wanted) return;
		let current = true;
		unwrap(api.GET('/api/v1/tags')).then(
			(every) => {
				if (current) nestedTags = every;
			},
			(failure) => {
				if (!current) return;
				toast.error(describeError(failure));
				masterDataDialogs.finishTag(null);
			}
		);
		return () => {
			current = false;
		};
	});

	async function submitNested(values: Values) {
		if (!nested) return;
		const tag = (await KINDS[kindOfScope(nested.scope)].create(values)) as Tag;
		toast.success(m.master_created());
		masterDataDialogs.saved();
		masterDataDialogs.finishTag(tag);
	}

	async function remove() {
		if (!simple || !item) return;
		await KINDS[simple].remove(item.id);
		toast.success(m.master_deleted());
		masterDataDialogs.close();
		masterDataDialogs.saved();
	}

	async function submit(values: Values) {
		const kind = anyTag && values.scope ? kindOfScope(values.scope) : simple;
		if (!kind) return;
		const ops = KINDS[kind];
		const id = item ? item.id : null;
		if (id) await ops.change(id, values);
		const saved = id ?? (await ops.create(values)).id;
		// A document type's fields, after the type: a new one needs its id first.
		if (values.fields) {
			await unwrap(
				api.PUT('/api/v1/document-types/{id}/fields', {
					params: { path: { id: saved } },
					body: { fields: values.fields.map(({ id, required }) => ({ field_id: id, required })) }
				})
			);
		}
		toast.success(id ? m.master_saved() : m.master_created());
		masterDataDialogs.saved();
	}
</script>

{#if simple}
	<MasterDataDialog
		bind:open={() => ready && request !== null, (value) => !value && masterDataDialogs.close()}
		title={item ? m.master_edit({ name: item.name }) : KINDS[simple].add()}
		shortTitle={item ? NOUN[simple]() : KINDS[simple].add()}
		extra={KINDS[simple].extra}
		initial={item}
		submitLabel={item ? m.save() : m.create()}
		ruleHref={ruleId ? `${BASE}/rules/${ruleId}` : null}
		{fields}
		{tags}
		scope={isTagKind(simple) ? TAG_KINDS[simple] : null}
		{fetching}
		onlogo={() => masterDataDialogs.saved()}
		ontagcreated={created}
		onsubmit={submit}
		ondelete={remove}
		deleteDescription={DELETE_HINT[simple]()}
	/>
{:else if anyTag}
	<MasterDataDialog
		bind:open={() => ready && request !== null, (value) => !value && masterDataDialogs.close()}
		title={m.master_add_tag()}
		extra="tag"
		submitLabel={m.create()}
		{tags}
		chooseScope
		onsubmit={submit}
	/>
{:else if request?.kind === 'fields'}
	<FieldDialog
		bind:open={() => ready && request !== null, (value) => !value && masterDataDialogs.close()}
		initial={field}
		{types}
		onsaved={() => masterDataDialogs.saved()}
	/>
{/if}

{#if nested}
	<MasterDataDialog
		bind:open={() => nestedTags !== null, (value) => !value && masterDataDialogs.finishTag(null)}
		title={KINDS[kindOfScope(nested.scope)].add()}
		extra="tag"
		presetName={nested.name}
		submitLabel={m.create()}
		tags={nestedTags ?? []}
		scope={nested.scope}
		onsubmit={submitNested}
	/>
{/if}
