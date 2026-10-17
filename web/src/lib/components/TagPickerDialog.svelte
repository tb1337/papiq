<script lang="ts">
	import SearchIcon from '@lucide/svelte/icons/search';
	import type { Snippet } from 'svelte';
	import TagChip from '#lib/components/TagChip.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import type { Tag, TagScope } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { phone } from '#lib/phone.svelte.ts';
	import { matchesQuery, ofScope, pathLabel } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// Several tags of one scope, chosen in a dialog over the editor it is opened from (on a
	// phone a sheet from the bottom): a search over their paths, then every tag of the scope
	// with a checkbox, nested as in its tree; while searching the matches with their paths, or
	// all of them when none matches. `actions` stand between the search and the list (the
	// offers to create a tag, which need the query).
	let {
		open = $bindable(false),
		title,
		scope,
		tags,
		selected,
		onchange,
		query = $bindable(''),
		searchLabel = m.assignment_search(),
		actions
	}: {
		open?: boolean;
		title: string;
		scope: TagScope;
		tags: readonly Tag[];
		selected: readonly string[];
		onchange: (selected: string[]) => void;
		query?: string;
		searchLabel?: string;
		actions?: Snippet;
	} = $props();

	let search = $state<HTMLInputElement | null>(null);
	const own = $derived(ofScope(tags, scope));
	const searching = $derived(query.trim() !== '');
	const matches = $derived(searching ? own.filter((tag) => matchesQuery(tag, query)) : own);
	// Nothing found: all of them, to choose from anyway.
	const listed = $derived(searching && matches.length === 0 ? own : matches);
	const isGroup = $derived(scope === 'contact' || scope === 'document_type');

	function toggle(id: string, on: boolean) {
		onchange(on ? [...selected, id] : selected.filter((entry) => entry !== id));
	}
</script>

<!-- However it closes, the next time it opens with an empty search: bits-ui reports only its own
     closes (Escape, outside), "Fertig" clears it itself. -->
<Dialog.Root bind:open onOpenChange={(value) => !value && (query = '')}>
	<Dialog.Content
		showCloseButton={false}
		onOpenAutoFocus={(event) => {
			// Wider screens start in the search; a phone's keyboard waits for a tap.
			if (phone.current) return;
			event.preventDefault();
			search?.focus();
		}}
		class={cn(
			'flex flex-col gap-0 overflow-hidden p-0',
			phone.current
				? 'top-auto bottom-0 left-0 h-[85dvh] max-h-[85dvh] w-full max-w-none translate-x-0 translate-y-0 rounded-t-2xl rounded-b-none data-[state=open]:slide-in-from-bottom-10'
				: 'h-[min(36rem,calc(100dvh-2rem))] sm:max-w-md'
		)}
	>
		{#if phone.current}
			<span class="mx-auto mt-2 h-1.5 w-10 shrink-0 rounded-full bg-border" aria-hidden="true"
			></span>
		{/if}
		<div class="flex shrink-0 items-center justify-between gap-2 py-2 pr-2 pl-4">
			<Dialog.Title class="text-base font-semibold">{title}</Dialog.Title>
			<Button
				variant="ghost"
				class="h-11 px-3 text-base font-semibold text-brand"
				onclick={() => {
					query = '';
					open = false;
				}}
			>
				{m.picker_done()}
			</Button>
		</div>
		<div class="shrink-0 px-4 pb-3">
			<label
				class="flex h-11 items-center gap-2 rounded-xl bg-muted px-3 text-muted-foreground focus-within:ring-2 focus-within:ring-ring/40"
			>
				<SearchIcon class="size-4 shrink-0" aria-hidden="true" />
				<input
					bind:this={search}
					bind:value={query}
					type="search"
					aria-label={searchLabel}
					placeholder={searchLabel}
					class="h-full min-w-0 flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground sm:text-sm"
				/>
			</label>
		</div>
		<div class="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto overscroll-contain px-4 pb-4">
			{@render actions?.()}
			{#if searching && matches.length === 0}
				<p class="px-1 text-sm text-muted-foreground">{m.picker_no_match()}</p>
				<h3 class="mt-2 px-1 text-xs font-medium tracking-wide text-muted-foreground uppercase">
					{m.picker_all({ kind: title })}
				</h3>
			{/if}
			{#if listed.length > 0}
				<ul class="divide-y overflow-hidden rounded-2xl border bg-card">
					{#each listed as tag (tag.id)}
						{@const nested = !searching || matches.length === 0}
						<li>
							<label
								class="flex min-h-12 cursor-pointer items-center gap-3 py-1.5 pr-3.5 pl-[calc(0.875rem+var(--depth)*1.5rem)] hover:bg-secondary/60"
								style="--depth: {nested ? tag.path.length - 1 : 0}"
							>
								<span class="flex min-w-0 flex-1 flex-wrap items-center gap-x-2 gap-y-0.5">
									<TagChip
										tag={{ ...tag, path: [tag.name] }}
										group={isGroup}
										topic={scope === 'topic'}
									/>
									{#if !nested && tag.path.length > 1}
										<span class="text-xs text-muted-foreground">{pathLabel(tag)}</span>
									{/if}
								</span>
								<input
									type="checkbox"
									class="size-5 shrink-0 accent-primary"
									aria-label={pathLabel(tag)}
									checked={selected.includes(tag.id)}
									onchange={(event) => toggle(tag.id, event.currentTarget.checked)}
								/>
							</label>
						</li>
					{/each}
				</ul>
			{/if}
		</div>
	</Dialog.Content>
</Dialog.Root>
