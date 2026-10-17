<script lang="ts">
	import LayoutGrid from '@lucide/svelte/icons/layout-grid';
	import List from '@lucide/svelte/icons/list';
	import SearchIcon from '@lucide/svelte/icons/search';
	import Upload from '@lucide/svelte/icons/upload';
	import X from '@lucide/svelte/icons/x';
	import { onMount, tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import DocumentCard from '#lib/components/DocumentCard.svelte';
	import DocumentFilters from '#lib/components/DocumentFilters.svelte';
	import DocumentRow from '#lib/components/DocumentRow.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import UploadDialog from '#lib/components/UploadDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import { apiQuery, isFiltered, parseFilters, writeFilters, type Filters } from '#lib/filters.ts';
	import { formatNumber } from '#lib/i18n.ts';
	import {
		LIST_SORTS,
		SEARCH_SORTS,
		rememberSort,
		rememberView,
		rememberedView,
		sortOf,
		type ListSort,
		type Sort,
		type View
	} from '#lib/listing.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { PagedList } from '#lib/paging.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { scope } from '#lib/scope.svelte.ts';
	import { uploads } from '#lib/upload.svelte.ts';
	import { cn } from '#lib/utils.ts';

	// The documents: listed (newest document date first) or, with words in `q`, searched (by
	// relevance), with the same filters; sorted as chosen, as a grid or a list. The URL holds
	// the words, the filters and the sort; grid or list and the last sort are remembered.
	type Document = components['schemas']['DocumentDetails'];
	type Segment = components['schemas']['SnippetSegment'];
	type Item = { document: Document; snippet: Segment[] };

	const params = $derived(new URLSearchParams(page.url.search));
	const filters = $derived(parseFilters(params));
	const query = $derived(params.get('q')?.trim() ?? '');
	const searching = $derived(query !== '');
	const sort = $derived(sortOf(params, searching));
	// The search field follows the URL (back button, links) until the user types.
	let text = $derived(query);
	let searchField = $state<HTMLInputElement | null>(null);
	let view = $state<View>(rememberedView());
	let lookup = $state<Lookup | null>(null);
	let lookupError = $state<string | null>(null);
	let uploadOpen = $state(false);

	const list = new PagedList<Item>(async (after) => {
		const base = apiQuery(filters, scope.allUsers);
		if (searching) {
			const data = await unwrap(
				api.GET('/api/v1/documents/search', {
					params: {
						query: { ...base, q: query, sort, offset: (after as number | null) ?? 0 }
					}
				})
			);
			const items = data.items.map((hit) => ({ document: hit.document, snippet: hit.snippet }));
			return { items, next: data.next_offset, total: data.estimated_total };
		}
		const data = await unwrap(
			api.GET('/api/v1/documents', {
				params: {
					query: {
						...base,
						sort: sort as ListSort,
						cursor: (after as string | null) ?? undefined
					}
				}
			})
		);
		return {
			items: data.items.map((document) => ({ document, snippet: [] })),
			next: data.next_cursor
		};
	});

	// Reload when the words, filters or sort change, and after a reconnect of the event stream.
	$effect(() => {
		void page.url.search;
		void scope.allUsers;
		void events.generation;
		void list.reload();
	});

	onMount(() => {
		loadLookup().then(
			(value) => (lookup = value),
			(error) => (lookupError = describeError(error))
		);
		// Opened from the search icon of a narrow header: the field takes the focus.
		if (location.hash === '#search') void tick().then(() => searchField?.focus());
		let timer: ReturnType<typeof setTimeout> | undefined;
		// Events are thin: fetch the list again, at most once in a while.
		const refresh = () => {
			clearTimeout(timer);
			timer = setTimeout(() => void list.refresh(), 400);
		};
		const stop = events.subscribe(refresh);
		const settled = uploads.onSettled(refresh);
		return () => {
			clearTimeout(timer);
			stop();
			settled();
		};
	});

	function go(next: URLSearchParams) {
		const search = next.toString();
		void goto(search ? `?${search}` : page.url.pathname, { reset: false });
	}

	function change(next: Filters) {
		go(writeFilters(new URLSearchParams(page.url.search), next));
	}

	function search(term: string) {
		// Built and handed on at once, nothing reacts to it.
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const next = new URLSearchParams(page.url.search);
		if (term) next.set('q', term);
		else next.delete('q');
		// A list sort may not suit a search and back: the remembered one of the mode holds.
		next.delete('sort');
		go(next);
	}

	function sortBy(value: string) {
		const chosen = value as Sort;
		rememberSort(chosen, searching);
		// eslint-disable-next-line svelte/prefer-svelte-reactivity
		const next = new URLSearchParams(page.url.search);
		next.set('sort', chosen);
		go(next);
	}

	function show(next: View) {
		view = next;
		rememberView(next);
	}

	const SORT_LABELS: Record<Sort, () => string> = {
		relevance: m.sort_relevance,
		'-document_date': m.sort_document_date_desc,
		document_date: m.sort_document_date_asc,
		'-created_at': m.sort_created_desc,
		created_at: m.sort_created_asc,
		title: m.sort_title_asc,
		'-title': m.sort_title_desc
	};
	const sorts = $derived(
		(searching ? SEARCH_SORTS : LIST_SORTS).map((value) => ({
			value,
			label: SORT_LABELS[value]()
		}))
	);
</script>

<svelte:head><title>{m.nav_documents()} · p:api:q</title></svelte:head>

{#snippet snippetLine(item: Item)}
	{#if item.snippet.length > 0}
		<span class="line-clamp-2 text-sm text-muted-foreground">
			{#each item.snippet as part, index (index)}
				{#if part.match}
					<mark class="rounded bg-lane-yellow px-0.5 text-lane-yellow-foreground">{part.text}</mark>
				{:else}{part.text}{/if}
			{/each}
		</span>
	{/if}
{/snippet}

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_documents()}</h1>
		<Button onclick={() => (uploadOpen = true)}>
			<Upload aria-hidden="true" />
			{m.upload_title()}
		</Button>
	</div>

	<div class="flex flex-col gap-3">
		<form
			role="search"
			class="flex h-11 items-center gap-2 rounded-full border border-input bg-background pr-1.5 pl-4 focus-within:border-ring"
			onsubmit={(event) => {
				event.preventDefault();
				search(text.trim());
			}}
		>
			<SearchIcon class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
			<input
				bind:this={searchField}
				bind:value={text}
				type="search"
				aria-label={m.documents_search_label()}
				placeholder={m.search_placeholder()}
				class="h-full min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden"
			/>
			{#if query}
				<Button
					type="button"
					variant="ghost"
					size="icon"
					class="size-8"
					aria-label={m.documents_search_clear()}
					onclick={() => {
						text = '';
						search('');
					}}
				>
					<X aria-hidden="true" />
				</Button>
			{/if}
		</form>
		{#if lookup}
			<DocumentFilters {filters} {lookup} onchange={change} />
		{:else if lookupError}
			<p class="text-destructive" role="alert">{lookupError}</p>
		{/if}
		<div class="flex flex-wrap items-center gap-3">
			{#if searching && list.total !== null && list.loaded}
				<p class="text-sm text-muted-foreground" aria-live="polite">
					{m.search_total({ count: formatNumber(list.total) })}
				</p>
			{/if}
			<div class="ml-auto flex w-full items-center gap-2 sm:w-auto">
				<NativeSelect
					aria-label={m.sort_label()}
					value={sort}
					options={sorts}
					onchange={sortBy}
					class="h-9 min-w-0 flex-1 sm:w-64 sm:flex-none"
				/>
				<div
					role="group"
					aria-label={m.view_label()}
					class="flex gap-0.5 rounded-full bg-secondary p-0.75"
				>
					{#each [{ value: 'grid', label: m.view_grid, icon: LayoutGrid }, { value: 'list', label: m.view_list, icon: List }] as const as entry (entry.value)}
						<button
							type="button"
							aria-pressed={view === entry.value}
							aria-label={entry.label()}
							title={entry.label()}
							onclick={() => show(entry.value)}
							class={cn(
								'grid size-8 place-items-center rounded-full transition-colors',
								view === entry.value
									? 'bg-card text-foreground shadow-sm'
									: 'text-muted-foreground hover:text-foreground'
							)}
						>
							<entry.icon class="size-4" aria-hidden="true" />
						</button>
					{/each}
				</div>
			</div>
		</div>
	</div>

	{#if list.error}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{describeError(list.error)}</p>
			<Button variant="outline" onclick={() => list.reload()}>{m.retry()}</Button>
		</div>
	{:else if list.empty}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{isFiltered(filters)
				? m.documents_none_filtered()
				: searching
					? m.search_none()
					: m.documents_none()}
		</p>
	{:else}
		{#if view === 'grid'}
			<ul
				class="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4 2xl:grid-cols-5"
				aria-busy={list.loading}
			>
				{#each list.items as item (item.document.id)}
					<li class="min-w-0">
						<DocumentCard document={item.document} {lookup}>
							{@render snippetLine(item)}
						</DocumentCard>
					</li>
				{/each}
			</ul>
		{:else}
			<ul class="flex flex-col gap-2" aria-busy={list.loading}>
				{#each list.items as item (item.document.id)}
					<li>
						<DocumentRow document={item.document} {lookup}>
							{@render snippetLine(item)}
						</DocumentRow>
					</li>
				{/each}
			</ul>
		{/if}
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

<UploadDialog bind:open={uploadOpen} spaces={lookup?.spaces ?? []} users={lookup?.users ?? []} />
