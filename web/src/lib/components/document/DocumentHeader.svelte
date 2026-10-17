<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronLeft from '@lucide/svelte/icons/chevron-left';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Download from '@lucide/svelte/icons/download';
	import Ellipsis from '@lucide/svelte/icons/ellipsis';
	import FolderInput from '@lucide/svelte/icons/folder-input';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import ScanEye from '@lucide/svelte/icons/scan-eye';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import NamesLine from '#lib/components/NamesLine.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import ProcessingProgress from '#lib/components/ProcessingProgress.svelte';
	import TagChips from '#lib/components/TagChips.svelte';
	import { Button, buttonVariants } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import { Checkbox } from '#lib/components/ui/checkbox/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Label } from '#lib/components/ui/label/index.ts';
	import { inboxDocument, tabHref, type Neighbours } from '#lib/document-view.ts';
	import { reportError } from '#lib/errors.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { spaceLabel, type Lookup } from '#lib/masterdata.svelte.ts';
	import { NODE_LABELS } from '#lib/node-labels.ts';
	import { canManage, canMove, writableSpaces } from '#lib/permissions.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { progress, reprocessable, type GraphNode } from '#lib/pipeline.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';
	import { adviceText, visionAvailable, type Reading, type Vision } from '#lib/vision.ts';

	type Document = components['schemas']['DocumentDetails'];
	type ReprocessNode = components['schemas']['ReprocessNode'];

	// The head of the document view: the way back (to the inbox, with the place among the
	// documents there, or to the documents), title, lane and file, contact (logo or initials)
	// and type (icon), tags, and what one can do with it.
	let {
		document,
		lookup,
		graph,
		archive,
		inbox,
		vision = null,
		reading = null,
		onchanged
	}: {
		document: Document;
		lookup: Lookup | null;
		graph: readonly GraphNode[];
		/** The archive PDF exists. */
		archive: boolean;
		/** Opened from the inbox: the documents before and after this one. */
		inbox: Neighbours | null;
		/** The vision language model documents can be read with, if one is configured. */
		vision?: Vision | null;
		/** How the document's text was read, and whether the model is advised. */
		reading?: Reading | null;
		/** After an action that changed the document. */
		onchanged: () => void;
	} = $props();

	let confirmDelete = $state(false);
	let moveOpen = $state(false);
	let reprocessOpen = $state(false);
	let visionOpen = $state(false);
	// Reprocessing from OCR a document the vision model read: read with it again (asked).
	let visionAgain = $state(true);
	let target = $state('');
	let fromNode = $state<ReprocessNode>('ocr');
	let busy = $state(false);

	const base = $derived(`/api/v1/documents/${document.id}`);
	const user = $derived(session.user);
	const manageable = $derived(canManage(document, user));
	const movable = $derived(canMove(document, user));
	const status = $derived(document.processing.status);
	const contact = $derived(lookup?.contacts.find((entry) => entry.id === document.contact_id));
	const kind = $derived(
		lookup?.documentTypes.find((entry) => entry.id === document.document_type_id)
	);
	const spaces = $derived(
		writableSpaces(lookup?.spaces ?? [], user).filter((space) => space.id !== document.space_id)
	);
	const spaceName = $derived(
		lookup?.spaces.find((space) => space.id === document.space_id)?.name ?? null
	);
	const running = $derived(progress(document.processing));
	// Nodes a reprocessing may start from: the graph decides (never past a failed node).
	const nodes = $derived(
		reprocessable(graph, document.processing)
			.filter((node): node is ReprocessNode => node !== 'receive')
			.map((node) => ({ value: node, label: NODE_LABELS[node]() }))
	);
	const inboxHref = (id: string) => inboxDocument(id);
	// Sending the pages to the vision model is the person's decision, here or from the advice.
	const readable = $derived(manageable && visionAvailable(vision, graph, document.processing));
	const advice = $derived(adviceText(reading));
	// The text recognition runs again on a document the model read last time: ask whether the
	// model reads it again, chosen by default, where it can.
	const askVision = $derived(fromNode === 'ocr' && (reading?.vision ?? false));
	const withVision = $derived(askVision && readable && visionAgain);
	const visionRunning = $derived(document.processing.vision && running.running.includes('ocr'));

	/** Ask whether the vision model shall read the document (also from the advice). */
	export function startVision() {
		if (readable) visionOpen = true;
	}

	/** Open the reprocessing dialog (also from the log). */
	export function startReprocess() {
		if (nodes.length === 0) return;
		fromNode = nodes[0].value;
		visionAgain = true;
		reprocessOpen = true;
	}

	function download(url: string) {
		const link = window.document.createElement('a');
		link.href = url;
		link.download = '';
		link.click();
	}

	async function act(action: () => Promise<unknown>, done: string): Promise<boolean> {
		busy = true;
		try {
			await action();
			toast.success(done);
			onchanged();
			return true;
		} catch (error) {
			reportError(error);
			return false;
		} finally {
			busy = false;
		}
	}

	async function retry() {
		await act(
			() =>
				unwrap(api.POST('/api/v1/documents/{id}/retry', { params: { path: { id: document.id } } })),
			m.retry_started()
		);
	}

	async function reprocess() {
		const params = { params: { path: { id: document.id } } };
		const done = await act(
			() =>
				withVision
					? unwrap(api.POST('/api/v1/documents/{id}/vision', params))
					: unwrap(
							api.POST('/api/v1/documents/{id}/reprocess', {
								...params,
								body: { from_node: fromNode }
							})
						),
			withVision ? m.vision_started() : m.reprocess_started()
		);
		if (done) reprocessOpen = false;
	}

	async function readWithVision() {
		await unwrap(
			api.POST('/api/v1/documents/{id}/vision', { params: { path: { id: document.id } } })
		);
		toast.success(m.vision_started());
		onchanged();
	}

	async function move() {
		if (!target) return;
		const done = await act(
			() =>
				unwrap(
					api.POST('/api/v1/documents/{id}/move', {
						params: { path: { id: document.id } },
						body: { space_id: target }
					})
				),
			m.move_done()
		);
		if (done) moveOpen = false;
	}

	async function remove() {
		await unwrap(api.DELETE('/api/v1/documents/{id}', { params: { path: { id: document.id } } }));
		toast.success(m.delete_done());
		await goto(inbox ? `${BASE}/inbox` : `${BASE}/documents`);
	}
</script>

<div class="flex flex-col gap-3.5">
	<div class="flex min-h-9 flex-wrap items-center gap-3">
		<a
			href={inbox ? `${BASE}/inbox` : `${BASE}/documents`}
			class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
		>
			<ArrowLeft class="size-4" aria-hidden="true" />
			{inbox ? m.nav_inbox() : m.nav_documents()}
		</a>
		{#if inbox && inbox.position !== null}
			<nav aria-label={m.doc_inbox_nav()} class="ml-auto flex items-center gap-1">
				<span class="mr-1.5 text-sm text-muted-foreground">
					{m.doc_inbox_position({ position: inbox.position, total: inbox.total })}
				</span>
				{#if inbox.previous}
					<a
						href={inboxHref(inbox.previous)}
						class={cn(buttonVariants({ variant: 'outline', size: 'icon' }), 'size-9')}
						aria-label={m.doc_previous()}
					>
						<ChevronLeft aria-hidden="true" />
					</a>
				{/if}
				{#if inbox.next}
					<a
						href={inboxHref(inbox.next)}
						class={cn(buttonVariants({ variant: 'outline', size: 'icon' }), 'size-9')}
						aria-label={m.doc_next()}
					>
						<ChevronRight aria-hidden="true" />
					</a>
				{/if}
			</nav>
		{/if}
	</div>

	<header class="flex flex-wrap items-start justify-between gap-4">
		<div class="flex min-w-0 flex-col gap-1.5">
			<div class="flex flex-wrap items-center gap-3">
				<h1 class="text-[1.75rem] leading-tight font-semibold tracking-tight break-words">
					{document.title}
				</h1>
				<LaneBadge lane={document.lane} />
			</div>
			<p class="text-sm text-muted-foreground">
				{document.original_filename} · {m.doc_uploaded({ date: formatDate(document.created_at) })}
				{#if spaceName}· {spaceName}{/if}
				{#if document.page_count}· {m.doc_pages({ count: document.page_count })}{/if}
			</p>
			<NamesLine
				parts={[
					{ name: contact?.name, avatar: contact },
					{ name: kind?.name, icon: kind?.icon }
				]}
				tags={lookup?.tags ?? []}
				class="text-sm"
			/>
			<TagChips {document} {lookup} interactive size="md" />
			{#if status === 'processing'}
				{#if visionRunning}
					<p class="text-sm text-muted-foreground" role="status">
						{m.doc_processing_vision({ done: running.done, total: running.total })}
					</p>
				{/if}
				<ProcessingProgress
					processing={document.processing}
					href={tabHref(page.url, 'log')}
					compact={visionRunning}
					class="max-w-md"
				/>
			{/if}
		</div>
		<div class="flex flex-wrap gap-2">
			<DropdownMenu.Root>
				<DropdownMenu.Trigger class={cn(buttonVariants({ variant: 'outline' }))}>
					<Download aria-hidden="true" />
					{m.doc_download()}
					<ChevronDown aria-hidden="true" />
				</DropdownMenu.Trigger>
				<DropdownMenu.Content align="end">
					<DropdownMenu.Item onSelect={() => download(`${base}/original`)}>
						{m.download_original()}
					</DropdownMenu.Item>
					{#if archive}
						<DropdownMenu.Item onSelect={() => download(`${base}/archive`)}>
							{m.download_archive()}
						</DropdownMenu.Item>
					{/if}
				</DropdownMenu.Content>
			</DropdownMenu.Root>
			{#if movable}
				<Button
					variant="outline"
					onclick={() => {
						target = spaces[0]?.id ?? '';
						moveOpen = true;
					}}
					disabled={spaces.length === 0}
				>
					<FolderInput aria-hidden="true" />
					{m.move_title()}
				</Button>
			{/if}
			{#if manageable}
				<DropdownMenu.Root>
					<DropdownMenu.Trigger
						class={cn(buttonVariants({ variant: 'outline', size: 'icon' }))}
						aria-label={m.doc_more()}
					>
						<Ellipsis aria-hidden="true" />
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="end" class="w-60">
						{#if status === 'failed'}
							<DropdownMenu.Item disabled={busy} onSelect={retry}>
								<RotateCcw aria-hidden="true" />
								{m.retry_node()}
							</DropdownMenu.Item>
						{/if}
						{#if status !== 'processing' && nodes.length > 0}
							<DropdownMenu.Item disabled={busy} onSelect={startReprocess}>
								<RefreshCw aria-hidden="true" />
								{m.reprocess_title()}
							</DropdownMenu.Item>
						{/if}
						{#if readable}
							<DropdownMenu.Item disabled={busy} onSelect={startVision}>
								<ScanEye aria-hidden="true" />
								{m.vision_read()}
								{#if advice}
									<span class="ml-auto text-xs text-lane-yellow-foreground">
										{m.vision_recommended()}
									</span>
								{/if}
							</DropdownMenu.Item>
						{/if}
						<DropdownMenu.Item variant="destructive" onSelect={() => (confirmDelete = true)}>
							<Trash2 aria-hidden="true" />
							{m.delete_title()}
						</DropdownMenu.Item>
					</DropdownMenu.Content>
				</DropdownMenu.Root>
			{/if}
		</div>
	</header>
</div>

<ConfirmDialog
	bind:open={confirmDelete}
	title={m.delete_title()}
	description={m.delete_confirm({ title: document.title })}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>

<Dialog.Root bind:open={moveOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>{m.move_title()}</Dialog.Title>
			<Dialog.Description>{m.move_hint()}</Dialog.Description>
		</Dialog.Header>
		<Field.Field>
			<Field.Label for="move-space">{m.field_space()}</Field.Label>
			<NativeSelect
				id="move-space"
				bind:value={target}
				options={spaces.map((space) => ({
					value: space.id,
					label: spaceLabel(space, lookup?.users ?? [], user?.id)
				}))}
			/>
		</Field.Field>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (moveOpen = false)}>{m.cancel()}</Button>
			<Button disabled={busy || !target} onclick={move}>{m.move_title()}</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

{#if vision}
	<ConfirmDialog
		bind:open={visionOpen}
		title={m.vision_title()}
		description={[advice, m.vision_hint({ endpoint: vision.endpoint, model: vision.model })]
			.filter(Boolean)
			.join(' ')}
		confirmLabel={m.vision_read()}
		destructive={false}
		onconfirm={readWithVision}
	/>
{/if}

<Dialog.Root bind:open={reprocessOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>{m.reprocess_title()}</Dialog.Title>
			<Dialog.Description>{m.reprocess_hint()}</Dialog.Description>
		</Dialog.Header>
		<Field.Field>
			<Field.Label for="reprocess-node">{m.reprocess_from()}</Field.Label>
			<NativeSelect id="reprocess-node" bind:value={fromNode} options={nodes} />
		</Field.Field>
		{#if askVision && readable && vision}
			<div class="flex flex-col gap-1.5">
				<div class="flex items-center gap-2">
					<Checkbox id="reprocess-vision" bind:checked={visionAgain} />
					<Label for="reprocess-vision">{m.reprocess_vision_again()}</Label>
				</div>
				<p class="pl-6 text-sm text-muted-foreground">
					{visionAgain
						? m.vision_hint({ endpoint: vision.endpoint, model: vision.model })
						: m.reprocess_vision_lost()}
				</p>
			</div>
		{:else if askVision}
			<p class="text-sm text-muted-foreground">{m.reprocess_vision_lost()}</p>
		{/if}
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (reprocessOpen = false)}>{m.cancel()}</Button>
			<Button disabled={busy} onclick={reprocess}>
				{withVision ? m.reprocess_with_vision() : m.reprocess_title()}
			</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
