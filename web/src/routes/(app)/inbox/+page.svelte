<script lang="ts">
	import { onMount } from 'svelte';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { BASE } from '#lib/base.ts';
	import DocumentRow from '#lib/components/DocumentRow.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { PagedList } from '#lib/paging.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	type Lane = 'yellow' | 'red';
	let lookup = $state<Lookup | null>(null);
	let lane = $state<Lane | null>(null);

	const list = new PagedList(async (cursor) => {
		const data = await unwrap(
			api.GET('/api/v1/inbox', {
				params: { query: { cursor: (cursor as string | null) ?? undefined } }
			})
		);
		return { items: data.items, next: data.next_cursor };
	});

	const shown = $derived(list.items.filter((item) => lane === null || item.document.lane === lane));

	$effect(() => {
		void events.generation;
		void list.reload();
	});

	onMount(() => {
		loadLookup().then(
			(value) => (lookup = value),
			() => {}
		);
		let timer: ReturnType<typeof setTimeout> | undefined;
		const stop = events.subscribe((event) => {
			if (event.type === 'document.received' || event.type === 'document.step_completed') return;
			clearTimeout(timer);
			timer = setTimeout(() => void list.refresh(), 400);
		});
		return () => {
			clearTimeout(timer);
			stop();
		};
	});

	const tabs: { lane: Lane | null; label: () => string }[] = [
		{ lane: null, label: m.inbox_all },
		{ lane: 'yellow', label: m.inbox_review },
		{ lane: 'red', label: m.inbox_intervene }
	];
</script>

<svelte:head><title>{m.nav_inbox()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_inbox()}</h1>

	<div class="flex flex-wrap gap-2">
		{#each tabs as tab (tab.lane)}
			<button
				type="button"
				aria-pressed={lane === tab.lane}
				onclick={() => (lane = tab.lane)}
				class={cn(
					'h-8 rounded-full border px-3 text-sm transition-colors',
					lane === tab.lane
						? 'border-primary bg-secondary font-medium'
						: 'text-muted-foreground hover:text-foreground'
				)}
			>
				{tab.label()}
			</button>
		{/each}
	</div>

	{#if list.error}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{describeError(list.error)}</p>
			<Button variant="outline" onclick={() => list.reload()}>{m.retry()}</Button>
		</div>
	{:else if list.empty || (list.loaded && shown.length === 0 && !list.hasMore)}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{m.inbox_empty()}
		</p>
	{:else}
		<ul class="flex flex-col gap-2" aria-busy={list.loading}>
			{#each shown as item (item.document.id)}
				<li>
					<DocumentRow document={item.document} {lookup} href="{BASE}/inbox/{item.document.id}">
						<span class="truncate text-sm">
							{item.open[0]?.reason ?? m.inbox_open_fields({ count: item.open.length })}
						</span>
					</DocumentRow>
				</li>
			{/each}
		</ul>
		{#if list.loading && !list.loaded}
			<p class="text-muted-foreground">{m.loading()}</p>
		{/if}
		{#if list.hasMore}
			<div class="flex justify-center">
				<Button variant="outline" disabled={list.loading} onclick={() => list.more()}>
					{m.load_more()}
				</Button>
			</div>
		{/if}
	{/if}
</div>
