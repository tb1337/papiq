<script lang="ts">
	import SearchIcon from '@lucide/svelte/icons/search';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import DocumentFilters from '#lib/components/DocumentFilters.svelte';
	import DocumentRow from '#lib/components/DocumentRow.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { apiQuery, isFiltered, parseFilters, writeFilters, type Filters } from '#lib/filters.ts';
	import { formatNumber } from '#lib/i18n.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { PagedList } from '#lib/paging.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { canListAllUsers } from '#lib/permissions.ts';
	import { session } from '#lib/session.svelte.ts';

	const query = $derived(page.url.searchParams.get('q')?.trim() ?? '');
	const filters = $derived(
		parseFilters(new URLSearchParams(page.url.search), canListAllUsers(session.user))
	);
	// The search box follows the URL (back button, links) until the user types.
	let text = $derived(query);
	let lookup = $state<Lookup | null>(null);

	const list = new PagedList(async (offset) => {
		const data = await unwrap(
			api.GET('/api/v1/documents/search', {
				params: {
					query: { ...apiQuery(filters), q: query, offset: (offset as number | null) ?? 0 }
				}
			})
		);
		return { items: data.items, next: data.next_offset, total: data.estimated_total };
	});
	// The total belongs to the page it came with: an answer the list discards is not counted.
	const total = $derived(list.total);

	$effect(() => {
		void page.url.search;
		if (query === '') {
			list.items = [];
			list.loaded = false;
			list.total = null;
		} else {
			void list.reload();
		}
	});

	onMount(() => {
		loadLookup().then(
			(value) => (lookup = value),
			() => {}
		);
	});

	function go(next: Filters, term: string) {
		const params = writeFilters(new URLSearchParams(page.url.search), next);
		if (term) params.set('q', term);
		else params.delete('q');
		void goto(`?${params}`, { reset: false });
	}
</script>

<svelte:head><title>{m.nav_search()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_search()}</h1>

	<form
		class="flex gap-2"
		role="search"
		onsubmit={(event) => {
			event.preventDefault();
			go(filters, text.trim());
		}}
	>
		<Input
			type="search"
			bind:value={text}
			placeholder={m.search_placeholder()}
			aria-label={m.search_placeholder()}
			class="h-12 flex-1"
		/>
		<Button type="submit" class="h-12">
			<SearchIcon aria-hidden="true" />
			{m.nav_search()}
		</Button>
	</form>

	{#if lookup}
		<DocumentFilters {filters} {lookup} onchange={(next) => go(next, query)} />
	{/if}

	{#if query === ''}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{m.search_hint()}
		</p>
	{:else if list.error}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{describeError(list.error)}</p>
			<Button variant="outline" onclick={() => list.reload()}>{m.retry()}</Button>
		</div>
	{:else if list.empty}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{isFiltered(filters) ? m.documents_none_filtered() : m.search_none()}
		</p>
	{:else}
		{#if total !== null && list.loaded}
			<p class="text-sm text-muted-foreground" aria-live="polite">
				{m.search_total({ count: formatNumber(total) })}
			</p>
		{/if}
		<ul class="flex flex-col gap-2" aria-busy={list.loading}>
			{#each list.items as item (item.document.id)}
				<li>
					<DocumentRow document={item.document} {lookup}>
						{#if item.snippet.length > 0}
							<span class="line-clamp-2 text-sm text-muted-foreground">
								{#each item.snippet as part, index (index)}
									{#if part.match}
										<mark class="rounded bg-lane-yellow px-0.5 text-lane-yellow-foreground">
											{part.text}
										</mark>
									{:else}{part.text}{/if}
								{/each}
							</span>
						{/if}
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
