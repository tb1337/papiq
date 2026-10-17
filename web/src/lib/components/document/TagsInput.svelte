<script lang="ts">
	import Plus from '@lucide/svelte/icons/plus';
	import TagChip from '#lib/components/TagChip.svelte';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import * as Command from '#lib/components/ui/command/index.ts';
	import * as Popover from '#lib/components/ui/popover/index.ts';
	import type { Tag } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';
	import { creatable, createQuickly, createWithDetails } from '#lib/tag-create.ts';
	import { above, matchesQuery, normalise, ofScope, pathLabel } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// The tags of a document: chips (the tag's own name, its path as tooltip and on a tap) to
	// remove, and a search to add a document tag, which finds a tag by the name of a tag above
	// it too. A tag added below one set replaces it, as the API keeps them; tags above one set
	// are not offered. `id` labels the button that adds. An admin's search that finds no tag
	// named so offers to create one (`tag-create.ts`), set at once.
	let {
		tags = $bindable(),
		all,
		id,
		disabled = false
	}: { tags: string[]; all: readonly Tag[]; id: string; disabled?: boolean } = $props();

	let open = $state(false);
	let query = $state('');
	// Tags made here, until `all` holds them too.
	let made = $state<Tag[]>([]);
	// A tag is being created: a second Enter or click must not send it again.
	let busy = $state(false);
	const known = $derived([
		...all,
		...made.filter((tag) => !all.some((other) => other.id === tag.id))
	]);
	const byId = $derived(new Map(known.map((tag) => [tag.id, tag])));
	// Neither the tags set nor those above them (the tag set says more).
	const free = $derived.by(() => {
		const higher = above(known, tags);
		return ofScope(known, 'document').filter(
			(tag) => !tags.includes(tag.id) && !higher.has(tag.id)
		);
	});
	const found = $derived(free.filter((tag) => matchesQuery(tag, query)));
	const offered = $derived(session.isAdmin && creatable(known, 'document', query));
	const addable = $derived(!disabled && (free.length > 0 || session.isAdmin));

	function chip(id: string): Pick<Tag, 'id' | 'name' | 'path'> {
		return byId.get(id) ?? { id, name: id, path: [id] };
	}

	function add(tag: string) {
		tags = normalise(known, [...tags, tag]);
		open = false;
		query = '';
	}

	async function quick() {
		if (busy) return;
		busy = true;
		const tag = await createQuickly('document', query);
		busy = false;
		if (!tag) return;
		made = [...made, tag];
		add(tag.id);
	}

	async function details() {
		const name = query;
		open = false;
		query = '';
		const tag = await createWithDetails('document', name);
		if (!tag) return;
		made = [...made, tag];
		add(tag.id);
	}
</script>

<!-- The chips carry `id` while there is no button to add one: the field's label points here. -->
{#if tags.length > 0 || !addable}
	<div
		id={addable ? undefined : id}
		role="group"
		aria-labelledby="{id}-label"
		class="flex flex-wrap gap-1.5"
	>
		{#each tags as tag (tag)}
			<TagChip
				tag={chip(tag)}
				interactive
				onremove={disabled ? undefined : () => (tags = tags.filter((entry) => entry !== tag))}
				removeLabel={m.filter_tag_remove({ name: pathLabel(chip(tag)) })}
			/>
		{/each}
	</div>
{/if}
{#if addable}
	<Popover.Root bind:open>
		<Popover.Trigger
			{id}
			class={cn(buttonVariants({ variant: 'outline', size: 'sm' }), 'w-fit')}
			aria-label={m.field_tag_add()}
		>
			<Plus aria-hidden="true" />
			{m.field_tag_add()}
		</Popover.Trigger>
		<Popover.Content class="w-72 p-0" align="start">
			<Command.Root shouldFilter={false}>
				<Command.Input
					bind:value={query}
					placeholder={session.isAdmin ? m.picker_search_create() : m.assignment_search()}
					aria-label={session.isAdmin ? m.picker_search_create() : m.assignment_search()}
				/>
				<Command.List>
					<Command.Empty class="py-4 text-center text-sm text-muted-foreground">
						{m.assignment_none_found()}
					</Command.Empty>
					{#each found as tag (tag.id)}
						<Command.Item
							value={tag.id}
							onSelect={() => add(tag.id)}
							class="flex-col items-start gap-0"
						>
							<span class="max-w-full truncate">{tag.name}</span>
							{#if tag.path.length > 1}
								<span class="max-w-full truncate text-xs text-muted-foreground">
									{pathLabel(tag)}
								</span>
							{/if}
						</Command.Item>
					{/each}
					{#if offered}
						<Command.Group class="border-t">
							<Command.Item
								value="create:quick"
								onSelect={quick}
								disabled={busy}
								class="text-brand"
							>
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
{/if}
