<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import Plus from '@lucide/svelte/icons/plus';
	import TagChip from '#lib/components/TagChip.svelte';
	import * as Command from '#lib/components/ui/command/index.ts';
	import * as Popover from '#lib/components/ui/popover/index.ts';
	import type { Tag } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { creatable, createQuickly, createWithDetails } from '#lib/tag-create.ts';
	import { matchesQuery, pathLabel } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// The topic of a document, or none: the chosen one as its chip, a search over `topics` (the
	// ones that may be chosen; the search finds a topic by the name of one above it too). Where
	// `creating` (an admin, with a contact), a search that finds no topic named so offers to
	// create one (`tag-create.ts`), chosen at once; `oncreated` tells the form.
	let {
		value = $bindable(''),
		topics,
		all,
		id,
		disabled = false,
		placeholder,
		creating = false,
		oncreated = () => {}
	}: {
		value?: string;
		topics: readonly Tag[];
		/** Every tag: a topic of another contact counts as named already. */
		all: readonly Tag[];
		id: string;
		disabled?: boolean;
		/** What the field says without a topic. */
		placeholder: string;
		creating?: boolean;
		oncreated?: (tag: Tag) => void;
	} = $props();

	let open = $state(false);
	let query = $state('');
	// Topics made here, until `all` and `topics` hold them too.
	let made = $state<Tag[]>([]);
	// A topic is being created: a second Enter or click must not send it again.
	let busy = $state(false);
	const fresh = $derived(made.filter((tag) => !all.some((other) => other.id === tag.id)));
	const known = $derived([...all, ...fresh]);
	const choices = $derived([
		...topics,
		...made.filter((tag) => !topics.some((other) => other.id === tag.id))
	]);
	const chosen = $derived(known.find((tag) => tag.id === value) ?? null);
	const found = $derived(choices.filter((tag) => matchesQuery(tag, query)));
	const offered = $derived(creating && creatable(known, 'topic', query));
	const searchLabel = $derived(creating ? m.picker_search_create() : m.assignment_search());

	function pick(topic: string) {
		value = topic;
		open = false;
		query = '';
	}

	function chooseMade(tag: Tag) {
		made = [...made, tag];
		oncreated(tag);
		pick(tag.id);
	}

	async function quick() {
		if (busy) return;
		busy = true;
		const tag = await createQuickly('topic', query);
		busy = false;
		if (tag) chooseMade(tag);
	}

	async function details() {
		const name = query;
		open = false;
		query = '';
		const tag = await createWithDetails('topic', name);
		if (tag) chooseMade(tag);
	}
</script>

<Popover.Root bind:open>
	<Popover.Trigger
		{id}
		{disabled}
		data-value={value}
		aria-label="{m.field_topic()}: {chosen ? pathLabel(chosen) : placeholder}"
		class="flex h-11 w-full min-w-0 items-center gap-2 rounded-xl border border-input bg-card px-3 text-left text-base transition-colors focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50 md:text-sm dark:bg-input/30"
	>
		{#if chosen}
			<TagChip tag={chosen} topic size="sm" />
		{:else}
			<span class="min-w-0 flex-1 truncate text-muted-foreground">{placeholder}</span>
		{/if}
		<ChevronDown class="ml-auto size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
	</Popover.Trigger>
	<Popover.Content class="w-(--bits-popover-anchor-width) min-w-64 p-0" align="start">
		<Command.Root shouldFilter={false}>
			<Command.Input bind:value={query} placeholder={searchLabel} aria-label={searchLabel} />
			<Command.List>
				<Command.Empty class="py-4 text-center text-sm text-muted-foreground">
					{m.assignment_none_found()}
				</Command.Empty>
				{#if query.trim() === ''}
					<Command.Item value="none" onSelect={() => pick('')}>
						<span class="flex-1">{m.topic_none()}</span>
						{#if value === ''}<Check aria-hidden="true" />{/if}
					</Command.Item>
				{/if}
				{#each found as tag (tag.id)}
					<Command.Item value={tag.id} onSelect={() => pick(tag.id)}>
						<span class="flex min-w-0 flex-1 flex-col">
							<span class="truncate">{tag.name}</span>
							{#if tag.path.length > 1}
								<span class="truncate text-xs text-muted-foreground">{pathLabel(tag)}</span>
							{/if}
						</span>
						{#if value === tag.id}<Check aria-hidden="true" />{/if}
					</Command.Item>
				{/each}
				{#if offered}
					<Command.Group class={cn(found.length > 0 && 'border-t')}>
						<Command.Item value="create:quick" onSelect={quick} disabled={busy} class="text-brand">
							<Plus aria-hidden="true" class="text-brand" />
							{m.tag_create_quick({ name: query.trim() })}
						</Command.Item>
						<Command.Item value="create:details" onSelect={details} class="pl-8 text-brand">
							{m.tag_create_details()}
						</Command.Item>
					</Command.Group>
				{/if}
			</Command.List>
		</Command.Root>
	</Popover.Content>
</Popover.Root>
