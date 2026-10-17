<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { BASE } from '#lib/base.ts';
	import ContactAvatar from '#lib/components/ContactAvatar.svelte';
	import MasterRow from '#lib/components/MasterRow.svelte';
	import TagChip from '#lib/components/TagChip.svelte';
	import TypeIcon from '#lib/components/TypeIcon.svelte';
	import { describeError } from '#lib/errors.ts';
	import { contactMatches, typeMatches } from '#lib/master-search.ts';
	import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import { type Item, KINDS } from '#lib/masterdata-api.ts';
	import type { FieldDefinition, Tag, TagScope } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { phone } from '#lib/phone.svelte.ts';
	import { scope } from '#lib/scope.svelte.ts';
	import { below, pathLabel } from '#lib/tags.ts';

	// Contacts (with their logo or initials, groups, aliases, identifiers, topics) or document
	// types (with their icon, description, groups, title pattern, fields), found by the search of
	// the page. A link from the Tags tab lists the contacts of a group or topic, the types of a
	// group (`?group=`, `?topic=`, with the ones below it). A row opens the editor.
	let { kind, query = '' }: { kind: 'contacts' | 'document-types'; query?: string } = $props();
	const ops = $derived(KINDS[kind]);

	let items = $state<Item[] | null>(null);
	let tags = $state<Tag[]>([]);
	let fields = $state<FieldDefinition[]>([]);
	let problem = $state<string | null>(null);

	const byId = $derived(new Map(tags.map((tag) => [tag.id, tag])));
	/** `id` if it names a tag of `scope`; else null: the filter is ignored. */
	function known(id: string | null, scope: TagScope): string | null {
		return id !== null && byId.get(id)?.scope === scope ? id : null;
	}
	const group = $derived(
		known(page.url.searchParams.get('group'), kind === 'contacts' ? 'contact' : 'document_type')
	);
	const topic = $derived(
		kind === 'contacts' ? known(page.url.searchParams.get('topic'), 'topic') : null
	);
	const filterTag = $derived(byId.get(group ?? topic ?? '') ?? null);
	const listed = $derived.by(() => {
		let list = items ?? [];
		const within = (wanted: string, ids: readonly string[] | undefined) => {
			const tree = below(tags, wanted).add(wanted);
			return (ids ?? []).some((id) => tree.has(id));
		};
		if (group) list = list.filter((item) => within(group, item.group_ids));
		if (topic) list = list.filter((item) => within(topic, item.topic_ids));
		if (query.trim()) {
			list = list.filter((item) =>
				kind === 'contacts' ? contactMatches(item, query) : typeMatches(item, query)
			);
		}
		return list;
	});

	async function load() {
		try {
			[items, tags, fields] = await Promise.all([
				ops.list({ counts: true, allUsers: scope.allUsers }),
				unwrap(api.GET('/api/v1/tags')),
				kind === 'document-types' ? unwrap(api.GET('/api/v1/fields')) : Promise.resolve([])
			]);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	// Once the tags are there, a filter naming no tag of its kind (deleted, mistyped) leaves the
	// URL instead of emptying the list without a chip to remove.
	$effect(() => {
		if (items === null) return;
		const params = page.url.searchParams;
		const stale =
			(params.has('group') && group === null) ||
			(kind === 'contacts' && params.has('topic') && topic === null);
		if (!stale) return;
		const kept = [`tab=${kind}`, group && `group=${group}`, topic && `topic=${topic}`];
		void goto(`?${kept.filter(Boolean).join('&')}`, { replace: true, reset: false });
	});

	// A link to a document type opens it.
	onMount(() => {
		const wanted = kind === 'document-types' ? page.url.searchParams.get('type') : null;
		if (wanted) masterDataDialogs.open('document-types', wanted);
	});

	// Again after a dialog saved (wherever it was opened), and with another scope.
	$effect(() => {
		void masterDataDialogs.changed;
		void scope.allUsers;
		void load();
	});

	/** An item's groups, by path. */
	function groupsOf(item: Item): Tag[] {
		return (item.group_ids ?? [])
			.map((id) => byId.get(id))
			.filter((tag): tag is Tag => tag !== undefined)
			.sort((a, b) => pathLabel(a).localeCompare(pathLabel(b)));
	}

	/** What a row tells under the name besides the groups; a type's title pattern in full as a
	 * tooltip. */
	function notesOf(item: Item): { text: string; title?: string }[] {
		if (kind === 'contacts') return contactNotes(item).map((text) => ({ text }));
		const notes: { text: string; title?: string }[] = [];
		if (item.title_pattern)
			notes.push({ text: m.master_title_pattern(), title: item.title_pattern });
		const fieldsText = fieldsNote(item);
		if (fieldsText) notes.push({ text: fieldsText });
		return notes;
	}

	/** Identifiers and topics; an alias where there is room (on a phone only without groups). */
	function contactNotes(item: Item): string[] {
		const aliases = phone.current && (item.group_ids?.length ?? 0) > 0 ? [] : (item.aliases ?? []);
		const identifiers = (item.identifiers ?? []).filter((entry) => entry.source !== 'rejected');
		const topics = item.topic_ids?.length ?? 0;
		const notes: (string | null)[] = [
			identifiers.length === 1
				? m.master_identifier_one()
				: identifiers.length > 1
					? m.master_identifiers_many({ count: identifiers.length })
					: null,
			topics === 1
				? m.master_topic_one()
				: topics > 1
					? m.master_topics_many({ count: topics })
					: null,
			aliases.length === 1
				? m.master_alias_short({ alias: aliases[0] })
				: aliases.length > 1
					? m.master_alias_more({ alias: aliases[0], count: aliases.length - 1 })
					: null
		];
		return notes.filter((note) => note !== null);
	}

	/** A type's own fields, and how many of them are required. */
	function fieldsNote(item: Item): string | null {
		const own = fields.filter((field) => field.document_type_ids?.includes(item.id));
		if (own.length === 0) return null;
		const required = own.filter((field) => field.required_document_type_ids.includes(item.id));
		const count =
			own.length === 1 ? m.master_field_one() : m.master_fields_many({ count: own.length });
		return required.length > 0
			? `${count} · ${m.master_required_count({ count: required.length })}`
			: count;
	}
</script>

<div class="flex flex-col gap-3">
	{#if filterTag}
		{@const label =
			group !== null
				? m.master_filter_group({ name: pathLabel(filterTag) })
				: m.master_filter_topic({ name: pathLabel(filterTag) })}
		<div>
			<span
				class="inline-flex h-9 max-w-full items-center gap-1 rounded-full border bg-card pr-1 pl-3.5 text-sm max-sm:h-11"
			>
				<span class="min-w-0 truncate">{label}</span>
				<button
					type="button"
					class="relative grid size-7 shrink-0 place-items-center rounded-full after:absolute after:-inset-1 hover:bg-secondary max-sm:size-9"
					aria-label={m.master_filter_remove({ name: label })}
					onclick={() => goto(`?tab=${kind}`, { reset: false })}
				>
					<X class="size-4" aria-hidden="true" />
				</button>
			</span>
		</div>
	{/if}
	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !items}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if listed.length === 0}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-10 text-center text-muted-foreground"
		>
			{items.length === 0 ? m.master_none() : m.assignment_none_found()}
		</p>
	{:else}
		<ul class="divide-y overflow-hidden rounded-2xl border bg-card" aria-label={ops.title()}>
			{#each listed as item (item.id)}
				{@const groups = groupsOf(item)}
				<MasterRow
					onopen={() => masterDataDialogs.open(kind, item)}
					editLabel={m.master_edit({ name: item.name })}
					usage={[
						{ count: item.document_count, href: `${BASE}/documents?${ops.filter}=${item.id}` }
					]}
				>
					{#snippet lead()}
						{#if kind === 'contacts'}
							<ContactAvatar contact={item} size="md" class="max-sm:size-10 max-sm:text-sm" />
						{:else}
							<span
								class="grid size-10 shrink-0 place-items-center rounded-xl bg-secondary text-foreground sm:size-8"
							>
								<TypeIcon icon={item.icon} class="size-5 sm:size-4" />
							</span>
						{/if}
					{/snippet}
					<span class="line-clamp-2 break-words">{item.name}</span>
					{#if kind === 'document-types' && item.description}
						<span class="line-clamp-2 text-sm text-muted-foreground">{item.description}</span>
					{/if}
					{#if groups.length > 0 || notesOf(item).length > 0}
						<span
							class="flex max-w-full flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground"
						>
							{#each groups as tag (tag.id)}<TagChip {tag} group size="sm" />{/each}
							{#each notesOf(item) as note (note.text)}
								<span title={note.title}>{note.text}</span>
							{/each}
						</span>
					{/if}
				</MasterRow>
			{/each}
		</ul>
	{/if}
</div>
