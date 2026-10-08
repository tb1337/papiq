<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import {
		LANE_FILTERS,
		NO_FILTERS,
		isFiltered,
		type Filters,
		type LaneFilter
	} from '#lib/filters.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	// The filters of the list and the search. The page stores them in the URL.
	let {
		filters,
		lookup,
		onchange
	}: { filters: Filters; lookup: Lookup; onchange: (filters: Filters) => void } = $props();

	const laneLabel: Record<LaneFilter, () => string> = {
		green: m.lane_green,
		yellow: m.lane_yellow,
		red: m.lane_red,
		processing: m.lane_running
	};
	const all = (label: string, list: readonly { id: string; name: string }[]) => [
		{ value: '', label },
		...list.map((entry) => ({ value: entry.id, label: entry.name }))
	];
	const tagName = $derived(new Map(lookup.tags.map((tag) => [tag.id, tag.name])));
	const freeTags = $derived(lookup.tags.filter((tag) => !filters.tags.includes(tag.id)));

	function set(patch: Partial<Filters>) {
		onchange({ ...filters, ...patch });
	}

	function toggleLane(lane: LaneFilter) {
		set({
			lanes: filters.lanes.includes(lane)
				? filters.lanes.filter((entry) => entry !== lane)
				: [...filters.lanes, lane]
		});
	}
</script>

<div class="flex flex-col gap-3">
	<div class="flex flex-wrap gap-3">
		<NativeSelect
			aria-label={m.filter_contact()}
			value={filters.contact ?? ''}
			options={all(m.filter_contact(), lookup.contacts)}
			onchange={(value) => set({ contact: value || null })}
			class="h-10 w-48"
		/>
		<NativeSelect
			aria-label={m.filter_type()}
			value={filters.type ?? ''}
			options={all(m.filter_type(), lookup.documentTypes)}
			onchange={(value) => set({ type: value || null })}
			class="h-10 w-48"
		/>
		<NativeSelect
			aria-label={m.filter_drawer()}
			value={filters.drawer ?? ''}
			options={all(m.filter_drawer(), lookup.drawers)}
			onchange={(value) => set({ drawer: value || null })}
			class="h-10 w-48"
		/>
		{#key filters.tags.length}
			<NativeSelect
				aria-label={m.filter_tag_add()}
				value=""
				options={all(m.filter_tag_add(), freeTags)}
				onchange={(value) => value && set({ tags: [...filters.tags, value] })}
				class="h-10 w-48"
			/>
		{/key}
	</div>
	<div class="flex flex-wrap items-center gap-2">
		{#each LANE_FILTERS as lane (lane)}
			<button
				type="button"
				aria-pressed={filters.lanes.includes(lane)}
				onclick={() => toggleLane(lane)}
				class={cn(
					'h-8 rounded-full border px-3 text-sm transition-colors',
					filters.lanes.includes(lane)
						? 'border-primary bg-secondary font-medium'
						: 'text-muted-foreground hover:text-foreground'
				)}
			>
				{laneLabel[lane]()}
			</button>
		{/each}
		{#each filters.tags as id (id)}
			<span class="inline-flex h-8 items-center gap-1 rounded-full bg-secondary pr-1 pl-3 text-sm">
				{tagName.get(id) ?? id}
				<button
					type="button"
					class="grid size-6 place-items-center rounded-full hover:bg-background"
					aria-label={m.filter_tag_remove({ name: tagName.get(id) ?? id })}
					onclick={() => set({ tags: filters.tags.filter((tag) => tag !== id) })}
				>
					<X class="size-3.5" aria-hidden="true" />
				</button>
			</span>
		{/each}
		{#if isFiltered(filters)}
			<Button variant="ghost" size="sm" onclick={() => onchange({ ...NO_FILTERS })}>
				{m.filter_reset()}
			</Button>
		{/if}
	</div>
</div>
