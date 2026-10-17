<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { ApiError } from '#lib/api/problem.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import DocumentContent from '#lib/components/document/DocumentContent.svelte';
	import DocumentFiles from '#lib/components/document/DocumentFiles.svelte';
	import DocumentForm from '#lib/components/document/DocumentForm.svelte';
	import DocumentLog from '#lib/components/document/DocumentLog.svelte';
	import DocumentHeader from '#lib/components/document/DocumentHeader.svelte';
	import PageImages from '#lib/components/document/PageImages.svelte';
	import PdfViewer from '#lib/components/PdfViewer.svelte';
	import * as Alert from '#lib/components/ui/alert/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import {
		inboxDocument,
		neighbours,
		tabHref,
		tabOf,
		type Mark,
		type Place,
		type Review,
		type Tab
	} from '#lib/document-view.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import { events, NODE_EVENTS } from '#lib/events.svelte.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import { canEdit, canManage } from '#lib/permissions.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { scope } from '#lib/scope.svelte.ts';
	import type { GraphNode } from '#lib/pipeline.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';
	import { factCounts, type CodeFacts, type CodePlace, type Facts } from '#lib/facts.ts';
	import { adviceText, visionAvailable, type Reading, type Vision } from '#lib/vision.ts';

	type Document = components['schemas']['DocumentDetails'];

	// One view for every document, from the inbox and from the list: the pages and the fields
	// (details), the recognised text and structure (content), and the processing (log).
	const id = $derived(page.params.id ?? '');
	const tab = $derived(tabOf(page.url));
	const fromInbox = $derived(page.url.searchParams.get('from') === 'inbox');
	let document = $state<Document | null>(null);
	let review = $state<Review | null>(null);
	let lookup = $state<Lookup | null>(null);
	let graph = $state<GraphNode[]>([]);
	let vision = $state<Vision | null>(null);
	let reading = $state<Reading | null>(null);
	let facts = $state<Facts | null>(null);
	// A code shown on its page from the content (`Auf der Seite zeigen`).
	let codeMarks = $state<Mark[]>([]);
	let inboxIds = $state<string[] | null>(null);
	let problem = $state<unknown>(null);
	let marks = $state<Mark[]>([]);
	let viewer = $state<PdfViewer | null>(null);
	let header = $state<DocumentHeader | null>(null);

	const user = $derived(session.user);
	const editable = $derived(document !== null && canEdit(document, user));
	const manageable = $derived(document !== null && canManage(document, user));
	const base = $derived(`/api/v1/documents/${id}`);
	// The archive exists once the text is recognised; until then the page images stand in.
	const archive = $derived(
		document?.processing.nodes.some(
			(state) => state.node === 'ocr' && state.status === 'done' && state.outcome !== 'failed'
		) ?? false
	);
	const inbox = $derived(fromInbox && inboxIds ? neighbours(inboxIds, id) : null);

	// An answer for a document the page has left behind, or one overtaken by a later request
	// (several events arrive at once), must not replace the current state.
	let latest = 0;
	async function load() {
		const wanted = id;
		const request = ++latest;
		try {
			const loaded = await unwrap(
				api.GET('/api/v1/documents/{id}', { params: { path: { id: wanted } } })
			);
			const params = { params: { path: { id: wanted } } };
			const [reviewed, read] = canManage(loaded, session.user)
				? await Promise.all([
						unwrap(api.GET('/api/v1/documents/{id}/review', params)),
						unwrap(api.GET('/api/v1/documents/{id}/reading', params))
					])
				: [null, null];
			const found = await loadFacts(wanted);
			if (wanted === id && request === latest) {
				document = loaded;
				review = reviewed;
				reading = read;
				facts = found;
				problem = null;
			}
		} catch (error) {
			if (wanted === id && request === latest) problem = error;
		}
	}

	/** The facts of the document; none while it has no text. */
	async function loadFacts(wanted: string): Promise<Facts | null> {
		try {
			return await unwrap(
				api.GET('/api/v1/documents/{id}/facts', { params: { path: { id: wanted } } })
			);
		} catch (error) {
			if (error instanceof ApiError && error.status === 409) return null;
			throw error;
		}
	}

	async function reloadFacts() {
		const wanted = id;
		try {
			const found = await loadFacts(wanted);
			if (wanted === id) facts = found;
		} catch (error) {
			reportError(error);
		}
	}

	/** Show a code where it stands: the details on `place`, every place of the code marked. */
	async function showCode(code: CodeFacts, place: CodePlace) {
		codeMarks = code.places.map((each) => ({
			key: `code:${each.page}`,
			label: code.format,
			page: each.page,
			boxes: each.boxes,
			derived: false,
			pinned: each.page === place.page
		}));
		await goto(tabHref(page.url, 'details'));
		// The viewer lays its pages out after it is shown.
		for (const wait of [300, 900, 1800]) setTimeout(() => viewer?.reveal(place), wait);
	}

	async function loadInbox() {
		try {
			const listed = await unwrap(
				api.GET('/api/v1/inbox', {
					params: { query: { limit: 100, all_users: scope.query } }
				})
			);
			inboxIds = listed.items.map((item) => item.document.id);
		} catch (error) {
			reportError(error);
		}
	}

	$effect(() => {
		void id;
		untrack(() => {
			if (document && document.id !== id) {
				document = null;
				review = null;
				reading = null;
				facts = null;
				marks = [];
				codeMarks = [];
			}
		});
	});

	$effect(() => {
		void id;
		void events.generation;
		void load();
	});

	$effect(() => {
		void events.generation;
		void scope.allUsers;
		if (fromInbox) untrack(() => void loadInbox());
	});

	// Again after a master data dialog saved here (a contact opened from its facts): names,
	// aliases and identifiers decide what the facts show.
	let savedBefore = masterDataDialogs.changed;
	$effect(() => {
		const saved = masterDataDialogs.changed;
		if (saved === savedBefore) return;
		savedBefore = saved;
		untrack(() => {
			loadLookup().then((value) => (lookup = value), reportError);
			void reloadFacts();
		});
	});

	onMount(() => {
		loadLookup().then((value) => (lookup = value), reportError);
		unwrap(api.GET('/api/v1/pipeline')).then((value) => {
			graph = value.nodes;
			vision = value.vision;
		}, reportError);
		return events.subscribe((event) => {
			if (event.document_id !== id) return;
			if (event.type === 'document.deleted')
				void goto(fromInbox ? `${BASE}/inbox` : `${BASE}/documents`);
			else void load();
			if (fromInbox && !NODE_EVENTS.has(event.type)) void loadInbox();
		});
	});

	function reveal(place: Place) {
		viewer?.reveal(place);
	}

	async function confirmed(next: string | null) {
		await goto(next ? inboxDocument(next) : `${BASE}/inbox`);
	}

	const tabs: { tab: Tab; label: () => string }[] = [
		{ tab: 'details', label: m.tab_details },
		{ tab: 'content', label: m.tab_content },
		{ tab: 'metadata', label: m.tab_metadata },
		{ tab: 'log', label: m.tab_log }
	];
	const attention = $derived(
		document?.processing.nodes.some(
			(state) => state.outcome === 'uncertain' || state.outcome === 'failed'
		) ?? false
	);
	const notFound = $derived(problem instanceof ApiError && problem.status === 404);
	// The text recognition made the document red or yellow: reading it with the vision model is
	// advised, and offered right here.
	const advice = $derived(
		document && manageable && visionAvailable(vision, graph, document.processing)
			? adviceText(reading)
			: null
	);
</script>

<svelte:head><title>{document?.title ?? m.nav_documents()} · p:api:q</title></svelte:head>

<!-- From the desktop width on the view fills the window; only its cards scroll. -->
<div class="flex flex-col gap-4 lg:h-full lg:min-h-0" data-wide>
	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{notFound ? m.document_not_found() : describeError(problem)}</p>
			{#if !notFound}<Button variant="outline" onclick={load}>{m.retry()}</Button>{/if}
		</div>
	{:else if !document}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<DocumentHeader
			bind:this={header}
			{document}
			{lookup}
			{graph}
			{archive}
			{inbox}
			{vision}
			{reading}
			onchanged={load}
		/>

		{#if advice}
			<Alert.Root variant="tip" role="status" class="flex flex-wrap items-center gap-3">
				<Alert.Description class="min-w-0 flex-1">{advice}</Alert.Description>
				<Button size="sm" onclick={() => header?.startVision()}>{m.vision_read()}</Button>
			</Alert.Root>
		{/if}

		<nav
			aria-label={m.tabs_label()}
			class="flex w-fit max-w-full gap-1 overflow-x-auto rounded-full bg-secondary p-1"
		>
			{#each tabs as entry (entry.tab)}
				<a
					href={tabHref(page.url, entry.tab)}
					aria-current={tab === entry.tab ? 'page' : undefined}
					class={cn(
						'inline-flex h-[34px] shrink-0 items-center gap-1.5 rounded-full px-3 text-sm whitespace-nowrap transition-colors sm:px-4',
						tab === entry.tab
							? 'bg-card font-medium text-foreground shadow-sm'
							: 'text-muted-foreground hover:text-foreground'
					)}
				>
					{entry.label()}
					{#if entry.tab === 'log' && attention}
						<span class="size-[7px] rounded-full bg-lane-yellow-foreground" aria-hidden="true"
						></span>
						<span class="sr-only">{m.tab_log_attention()}</span>
					{/if}
				</a>
			{/each}
		</nav>

		{#if tab === 'details'}
			<div
				class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_28rem] lg:grid-rows-[minmax(0,1fr)]"
			>
				<div class="flex min-h-[32rem] flex-col lg:min-h-0">
					<!-- Anew once the archive exists: the marks belong to it, not to the original. -->
					{#key `${id}:${archive}`}
						{#if archive || !document.page_count}
							<PdfViewer
								bind:this={viewer}
								sources={archive || document.media_type !== 'application/pdf'
									? [`${base}/archive`]
									: [`${base}/archive`, `${base}/original`]}
								label={document.title}
								marks={archive ? [...marks, ...codeMarks] : []}
							/>
						{:else}
							<PageImages documentId={id} pageCount={document.page_count} label={document.title} />
						{/if}
					{/key}
				</div>
				<div class="flex min-h-[32rem] flex-col lg:min-h-0">
					{#if lookup}
						{#key id}
							<DocumentForm
								{document}
								{lookup}
								{review}
								{editable}
								next={inbox?.next ?? null}
								onsaved={(updated) => (document = updated)}
								onenabled={() => loadLookup().then((value) => (lookup = value), reportError)}
								onconfirmed={confirmed}
								onmarks={(next) => (marks = next)}
								factCounts={facts ? factCounts(facts) : null}
								factsHref={tabHref(page.url, 'content')}
								onreveal={reveal}
							/>
						{/key}
					{:else}
						<p class="text-muted-foreground">{m.loading()}</p>
					{/if}
				</div>
			</div>
		{:else if tab === 'content'}
			<DocumentContent
				documentId={id}
				filename={document.original_filename}
				vision={reading?.vision ?? false}
				{facts}
				{lookup}
				topicId={document.topic_id}
				admin={session.isAdmin}
				onshowcode={showCode}
				onfactschanged={reloadFacts}
			/>
		{:else if tab === 'metadata'}
			<DocumentFiles documentId={id} />
		{:else if tab === 'log'}
			<div class="lg:min-h-0 lg:flex-1">
				<DocumentLog
					{document}
					{graph}
					{manageable}
					{lookup}
					onreprocess={() => header?.startReprocess()}
				/>
			</div>
		{/if}
	{/if}
</div>
