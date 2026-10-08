<script lang="ts">
	import Upload from '@lucide/svelte/icons/upload';
	import { onMount } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import DocumentFilters from '#lib/components/DocumentFilters.svelte';
	import DocumentThumb from '#lib/components/DocumentThumb.svelte';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import UploadDialog from '#lib/components/UploadDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { BASE } from '#lib/base.ts';
	import { describeError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import { apiQuery, isFiltered, parseFilters, writeFilters, type Filters } from '#lib/filters.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { loadLookup, names, type Lookup } from '#lib/masterdata.svelte.ts';
	import { PagedList } from '#lib/paging.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { uploads } from '#lib/upload.svelte.ts';

	const filters = $derived(parseFilters(new URLSearchParams(page.url.search)));
	let lookup = $state<Lookup | null>(null);
	let lookupError = $state<string | null>(null);
	let uploadOpen = $state(false);

	const list = new PagedList(async (cursor) => {
		const data = await unwrap(
			api.GET('/api/v1/documents', {
				params: { query: { ...apiQuery(filters), cursor: (cursor as string | null) ?? undefined } }
			})
		);
		return { items: data.items, next: data.next_cursor };
	});

	const contactName = $derived(names(lookup?.contacts ?? []));
	const typeName = $derived(names(lookup?.documentTypes ?? []));

	// Reload when the filters change, and after a reconnect of the event stream.
	$effect(() => {
		void page.url.search;
		void events.generation;
		void list.reload();
	});

	onMount(() => {
		loadLookup().then(
			(value) => (lookup = value),
			(error) => (lookupError = describeError(error))
		);
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

	function change(next: Filters) {
		const params = writeFilters(new URLSearchParams(page.url.search), next);
		void goto(`?${params}`, { reset: false });
	}
</script>

<svelte:head><title>{m.nav_documents()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_documents()}</h1>
		<Button onclick={() => (uploadOpen = true)}>
			<Upload aria-hidden="true" />
			{m.upload_title()}
		</Button>
	</div>

	{#if lookup}
		<DocumentFilters {filters} {lookup} onchange={change} />
	{:else if lookupError}
		<p class="text-destructive" role="alert">{lookupError}</p>
	{/if}

	{#if list.error}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{describeError(list.error)}</p>
			<Button variant="outline" onclick={() => list.reload()}>{m.retry()}</Button>
		</div>
	{:else if list.empty}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{isFiltered(filters) ? m.documents_none_filtered() : m.documents_none()}
		</p>
	{:else}
		<ul class="flex flex-col gap-2" aria-busy={list.loading}>
			{#each list.items as document (document.id)}
				<li>
					<a
						href="{BASE}/documents/{document.id}"
						class="flex items-center gap-4 rounded-2xl border bg-card p-3 transition-colors hover:border-primary"
					>
						<DocumentThumb id={document.id} class="h-20 w-14 shrink-0 rounded-lg border" />
						<div class="flex min-w-0 flex-1 flex-col gap-1">
							<span class="truncate font-medium">{document.title}</span>
							<span class="truncate text-sm text-muted-foreground">
								{[
									document.contact_id ? contactName.get(document.contact_id) : null,
									document.document_type_id ? typeName.get(document.document_type_id) : null,
									formatDate(document.document_date ?? document.created_at)
								]
									.filter(Boolean)
									.join(' · ')}
							</span>
						</div>
						<LaneBadge lane={document.lane} />
					</a>
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

<UploadDialog bind:open={uploadOpen} drawers={lookup?.drawers ?? []} />
