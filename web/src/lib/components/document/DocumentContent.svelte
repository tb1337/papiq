<script lang="ts">
	import Copy from '@lucide/svelte/icons/copy';
	import Download from '@lucide/svelte/icons/download';
	import { tick, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { ApiError } from '#lib/api/problem.ts';
	import FactsPanel from '#lib/components/document/FactsPanel.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import {
		inUnits,
		pageRanges,
		segmentsOf,
		type CodeFacts,
		type CodePlace,
		type Facts,
		type Span
	} from '#lib/facts.ts';
	import { formatNumber } from '#lib/i18n.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';

	// What was read from the document: the text as it is set, in a fixed-pitch font so that its
	// columns line up, page by page, and beside it the facts and codes. Two cards as in the
	// details: the text on the card itself, the facts on its inner surface. The text comes as
	// soon as the document is parsed. A document parsed before 0.2 shows Docling's Markdown as it
	// is until it is read again.
	let {
		documentId,
		filename,
		vision = false,
		facts = null,
		lookup = null,
		topicId = null,
		admin = false,
		onshowcode = () => {},
		onfactschanged = () => {}
	}: {
		documentId: string;
		filename: string;
		/** The vision language model read the text (a person asked for it). */
		vision?: boolean;
		/** What the document shows of its contact; beside the text when there. */
		facts?: Facts | null;
		lookup?: Lookup | null;
		/** The document's topic: identifiers attached point to it by default. */
		topicId?: string | null;
		/** Admins attach identifiers to contacts. */
		admin?: boolean;
		/** Show a code where it stands on its page (the details). */
		onshowcode?: (code: CodeFacts, place: CodePlace) => void;
		/** After an identifier was attached: the facts are read again. */
		onfactschanged?: () => void;
	} = $props();

	const PIECE = 20_000;
	// More than this is not shown (the download has all of it).
	const MOST_TEXT = 2_000_000;

	// What the facts mark in the text: spans in characters of the whole text.
	let marks = $state<Span[]>([]);
	let textBox = $state<HTMLDivElement | null>(null);
	let text = $state<string | null>(null);
	let total = $state(0);
	let problem = $state<string | null>(null);
	let loading = $state(true);

	type Piece = { text: string; total_length: number; next_offset: number | null };

	/** The text from `offset` on, up to `most` characters; with the total length. */
	async function readText(id: string, most: number): Promise<{ text: string; total: number }> {
		let offset: number | null = 0;
		let collected = '';
		let length = 0;
		while (offset !== null && collected.length < most) {
			const piece: Piece = await unwrap(
				api.GET('/api/v1/documents/{id}/text', {
					params: { path: { id }, query: { offset, limit: PIECE } }
				})
			);
			collected += piece.text;
			length = piece.total_length;
			offset = piece.next_offset;
		}
		return { text: collected, total: length };
	}

	// An answer overtaken by a later request (several events at once) must not replace the
	// current text.
	let latest = 0;
	async function loadText() {
		const wanted = documentId;
		const request = ++latest;
		loading = true;
		try {
			const read = await readText(wanted, MOST_TEXT);
			if (wanted !== documentId || request !== latest) return;
			text = read.text;
			total = read.total;
			problem = null;
		} catch (error) {
			if (wanted !== documentId || request !== latest) return;
			// 409: not parsed yet; the next event brings the text.
			if (error instanceof ApiError && error.status === 409) text = null;
			else problem = describeError(error);
		} finally {
			if (request === latest) loading = false;
		}
	}

	$effect(() => {
		void documentId;
		untrack(() => {
			text = null;
			problem = null;
		});
		void events.generation;
		void loadText();
		return events.subscribe((event) => {
			if (event.document_id === documentId && event.type === 'document.node_completed') {
				void loadText();
			}
		});
	});

	// The pages of the text: a form feed on a line of its own separates them.
	const pages = $derived((text ?? '').split(/\n?\f\n?/));
	const ranges = $derived(pageRanges(text ?? ''));
	const marked = $derived(text === null ? [] : inUnits(text, marks));

	/** Mark `spans` in the text; with `reveal`, scroll the first into view. */
	async function mark(spans: Span[], reveal: boolean) {
		marks = spans;
		if (!reveal || spans.length === 0) return;
		await tick();
		textBox?.querySelector('[data-mark]')?.scrollIntoView({ block: 'center' });
	}
	const stem = $derived(filename.replace(/\.[^.]+$/, '') || 'document');

	async function copy() {
		if (text === null) return;
		try {
			await navigator.clipboard.writeText(text);
			toast.success(m.content_copied());
		} catch (error) {
			toast.error(describeError(error));
		}
	}

	async function download() {
		if (text === null) return;
		let whole = text;
		if (text.length < total) {
			// What is shown stops short; the file has all of it.
			try {
				whole = (await readText(documentId, Infinity)).text;
			} catch (error) {
				toast.error(describeError(error));
				return;
			}
		}
		const link = window.document.createElement('a');
		const url = URL.createObjectURL(new Blob([whole], { type: 'text/plain;charset=utf-8' }));
		link.href = url;
		link.download = `${stem}.txt`;
		link.click();
		setTimeout(() => URL.revokeObjectURL(url), 1000);
	}
</script>

<div
	class="grid grid-cols-[minmax(0,1fr)] gap-6 lg:min-h-0 lg:flex-1 lg:grid-cols-[minmax(0,1fr)_28rem] lg:grid-rows-[minmax(0,1fr)]"
>
	<section
		aria-label={m.tab_content()}
		class="flex min-h-[32rem] min-w-0 flex-col overflow-hidden rounded-2xl border bg-card lg:min-h-0"
	>
		<div class="flex flex-wrap items-center gap-2 border-b px-3 py-2">
			<span class="px-1 text-sm text-muted-foreground">
				{#if text !== null}
					{m.text_length({ characters: formatNumber(total) })}
					{#if vision}· {m.vision_read_by()}{/if}
				{/if}
			</span>
			<div class="ml-auto flex gap-1">
				<Button variant="ghost" size="sm" disabled={text === null} onclick={copy}>
					<Copy aria-hidden="true" />
					{m.content_copy()}
				</Button>
				<Button variant="ghost" size="sm" disabled={text === null} onclick={download}>
					<Download aria-hidden="true" />
					{m.doc_download()}
				</Button>
			</div>
		</div>
		<!-- As set: no wrapping, the card scrolls sideways. -->
		<div bind:this={textBox} class="relative min-h-0 flex-1 overflow-auto">
			{#if problem}
				<p class="p-6 text-destructive" role="alert">{problem}</p>
			{:else if text === null}
				<p class="p-6 text-sm text-muted-foreground">{loading ? m.loading() : m.text_none()}</p>
			{:else}
				{#each pages as page, index (index)}
					<section aria-label={m.content_page({ page: index + 1 })} class="px-7 py-6">
						{#if pages.length > 1}
							<p class="mb-3 text-xs font-semibold tracking-wider text-muted-foreground uppercase">
								{m.content_page({ page: index + 1 })}
							</p>
						{/if}
						<pre
							class="font-mono text-[13px] leading-relaxed whitespace-pre">{#if marked.length > 0}{#each segmentsOf(text, ranges[index], marked) as segment, at (at)}{#if segment.marked}<mark
											data-mark
											class="rounded-sm bg-primary/20 text-inherit ring-2 ring-primary"
											>{segment.text}</mark
										>{:else}{segment.text}{/if}{/each}{:else}{page}{/if}</pre>
					</section>
					{#if index < pages.length - 1}<hr class="border-muted" />{/if}
				{/each}
			{/if}
		</div>
	</section>
	<aside
		aria-label={m.facts_title()}
		class="flex min-w-0 flex-col overflow-hidden rounded-2xl border bg-card lg:min-h-0"
	>
		<div class="relative min-h-0 flex-1 overflow-y-auto bg-surface p-3.5">
			{#if facts}
				<FactsPanel
					{facts}
					{documentId}
					{lookup}
					{topicId}
					{admin}
					onmark={mark}
					{onshowcode}
					onchanged={onfactschanged}
				/>
			{:else}
				<p class="p-2 text-sm text-muted-foreground">{m.facts_pending()}</p>
			{/if}
		</div>
	</aside>
</div>
