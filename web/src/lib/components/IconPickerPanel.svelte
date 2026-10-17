<script lang="ts">
	import { Input } from '#lib/components/ui/input/index.ts';
	import LucideIcon from '#lib/components/LucideIcon.svelte';
	import { GRID_KEYS, nextTile } from '#lib/grid-keys.ts';
	import { CURATED, CURATED_NAMES } from '#lib/icons/curated.ts';
	import { AREA_LABELS, iconLabel } from '#lib/icons/labels.ts';
	import { searchIcons } from '#lib/icons/search.ts';
	import { m } from '#lib/paraglide/messages.js';

	// The open part of the icon picker: the icons offered first by areas of life, and a search
	// over every Lucide icon (their names, and the words of the ones offered first in either
	// language). Loaded only when the picker opens: with it come the words and the index of all
	// icons (`search.ts`, `lucide.ts`). Icons load as they come into view. The tiles are one tab
	// stop (the chosen icon, else the first): the arrows move within them by rows, Home and End
	// to the ends of a row (with Ctrl or ⌘ of all), Enter or Space chooses.
	let {
		id,
		value,
		onchoose
	}: {
		id: string;
		value: string | null;
		onchoose: (name: string) => void;
	} = $props();

	let query = $state('');
	const found = $derived(query.trim() ? searchIcons(query) : null);
	// The tile that holds the tab stop: the one last focused while it is shown.
	let active = $state<string | null>(null);
	const shown = $derived(found ? found.names : CURATED_NAMES);
	const stop = $derived(
		active && shown.includes(active)
			? active
			: value && shown.includes(value)
				? value
				: (shown[0] ?? null)
	);

	function move(event: KeyboardEvent & { currentTarget: HTMLElement }) {
		if (!GRID_KEYS.has(event.key) || !(event.target instanceof HTMLElement)) return;
		const tiles = [...event.currentTarget.querySelectorAll<HTMLElement>('[data-icon-tile]')];
		const index = tiles.indexOf(event.target);
		if (index < 0) return;
		event.preventDefault();
		const boxes = tiles.map((tile) => tile.getBoundingClientRect());
		const next = nextTile(event.key, index, boxes, event.ctrlKey || event.metaKey);
		if (next !== null) tiles[next].focus();
	}
</script>

{#snippet tile(name: string)}
	<button
		type="button"
		class="grid aspect-square place-items-center rounded-lg border border-transparent text-foreground transition-colors hover:border-border hover:bg-secondary aria-pressed:border-primary aria-pressed:bg-primary/10"
		aria-label={iconLabel(name)}
		title={name}
		aria-pressed={value === name}
		tabindex={name === stop ? 0 : -1}
		data-icon-tile
		onfocus={() => (active = name)}
		onclick={() => onchoose(name)}
	>
		<LucideIcon {name} lazy class="size-5" />
	</button>
{/snippet}

<div id="{id}-panel" class="flex flex-col gap-3 rounded-xl border bg-card p-3">
	<Input
		type="search"
		bind:value={query}
		placeholder={m.icon_search()}
		aria-label={m.icon_search()}
	/>
	<!-- The arrows, Home and End bubble up from the tiles (buttons); the list takes no focus. -->
	<!-- svelte-ignore a11y_no_static_element_interactions -->
	<div class="flex max-h-72 flex-col gap-4 overflow-y-auto pr-1" onkeydown={move}>
		{#if found}
			{#if found.names.length === 0}
				<p class="text-sm text-muted-foreground">{m.icon_search_none()}</p>
			{:else}
				<div class="grid grid-cols-[repeat(auto-fill,minmax(2.75rem,1fr))] gap-1">
					{#each found.names as name (name)}{@render tile(name)}{/each}
				</div>
				{#if found.total > found.names.length}
					<p class="text-sm text-muted-foreground">
						{m.icon_search_more({ count: found.total - found.names.length })}
					</p>
				{/if}
			{/if}
		{:else}
			{#each CURATED as group (group.area)}
				<section class="flex flex-col gap-1.5" aria-label={AREA_LABELS[group.area]()}>
					<h4 class="text-xs font-medium text-muted-foreground">
						{AREA_LABELS[group.area]()}
					</h4>
					<div class="grid grid-cols-[repeat(auto-fill,minmax(2.75rem,1fr))] gap-1">
						{#each group.icons as name (name)}{@render tile(name)}{/each}
					</div>
				</section>
			{/each}
		{/if}
	</div>
</div>
