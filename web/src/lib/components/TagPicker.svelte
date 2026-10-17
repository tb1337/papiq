<script lang="ts">
	import Plus from '@lucide/svelte/icons/plus';
	import CreateOffers from '#lib/components/CreateOffers.svelte';
	import TagChip from '#lib/components/TagChip.svelte';
	import TagPickerDialog from '#lib/components/TagPickerDialog.svelte';
	import type { Tag, TagScope } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';
	import { creatable, createQuickly, createWithDetails } from '#lib/tag-create.ts';
	import { pathLabel } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// The groups of a contact or a type, the topics of a contact: chips to remove and a button
	// that opens the picker over the editor (`TagPickerDialog`). A chosen tag missing from
	// `tags` (deleted meanwhile) stays as a chip until removed. An admin's search that finds no
	// tag named so offers to create one (`tag-create.ts`), chosen at once; `oncreated` tells
	// the editor, whose `tags` should hold it.
	let {
		id,
		label,
		labelledby,
		title,
		scope,
		tags,
		selected,
		onchange,
		oncreated = () => {}
	}: {
		id: string;
		/** What the chips are ("Groups"). */
		label: string;
		/** The id of a visible label of the chips, named so instead of by `label`. */
		labelledby?: string;
		/** The picker's title, the tags of the scope ("Contact groups"). */
		title: string;
		scope: TagScope;
		tags: readonly Tag[];
		selected: readonly string[];
		onchange: (selected: string[]) => void;
		oncreated?: (tag: Tag) => void;
	} = $props();

	let open = $state(false);
	let query = $state('');
	let busy = $state(false);
	const offered = $derived(session.isAdmin && creatable(tags, scope, query));
	const byId = $derived(new Map(tags.map((tag) => [tag.id, tag])));
	const chips = $derived(
		[...new Set(selected)].map(
			(value) => byId.get(value) ?? { id: value, name: m.rule_unknown(), path: [m.rule_unknown()] }
		)
	);
	const isTopic = $derived(scope === 'topic');

	function chosen(tag: Tag) {
		oncreated(tag);
		onchange([...selected, tag.id]);
	}

	async function quick() {
		busy = true;
		const tag = await createQuickly(scope, query);
		busy = false;
		if (!tag) return;
		chosen(tag);
		query = '';
	}

	async function details() {
		const name = query;
		query = '';
		open = false;
		const tag = await createWithDetails(scope, name);
		if (tag) chosen(tag);
	}
</script>

<div
	{id}
	role="group"
	aria-label={labelledby ? undefined : label}
	aria-labelledby={labelledby}
	class="flex min-w-0 flex-wrap items-center gap-2 rounded-xl border border-input bg-card p-2 dark:bg-input/30"
>
	{#each chips as tag (tag.id)}
		<TagChip
			{tag}
			group={!isTopic}
			topic={isTopic}
			interactive
			onremove={() => onchange(selected.filter((entry) => entry !== tag.id))}
			removeLabel={m.rule_remove_value({ name: pathLabel(tag) })}
		/>
	{/each}
	<button
		type="button"
		aria-haspopup="dialog"
		aria-expanded={open}
		aria-label={isTopic ? m.picker_add_topic_label() : m.picker_add_group_label()}
		class={cn(
			'inline-flex h-8 items-center gap-1.5 border border-dashed border-muted-foreground/50 px-3 text-sm text-brand transition-colors hover:bg-secondary max-sm:h-11',
			isTopic ? 'rounded-md' : 'rounded-full'
		)}
		onclick={() => {
			query = '';
			open = true;
		}}
	>
		<Plus class="size-4" aria-hidden="true" />
		{isTopic ? m.picker_add_topic() : m.picker_add_group()}
	</button>
</div>

<TagPickerDialog
	bind:open
	bind:query
	{title}
	{scope}
	{tags}
	{selected}
	{onchange}
	searchLabel={session.isAdmin ? m.picker_search_create() : m.assignment_search()}
>
	{#snippet actions()}
		{#if offered}
			<CreateOffers name={query.trim()} {busy} onquick={quick} ondetails={details} />
		{/if}
	{/snippet}
</TagPickerDialog>
