<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import Download from '@lucide/svelte/icons/download';
	import FolderInput from '@lucide/svelte/icons/folder-input';
	import RefreshCw from '@lucide/svelte/icons/refresh-cw';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount, untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { ApiError } from '#lib/api/problem.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import DocumentEditor from '#lib/components/DocumentEditor.svelte';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import PdfViewer from '#lib/components/PdfViewer.svelte';
	import ProcessingLog from '#lib/components/ProcessingLog.svelte';
	import { Button, buttonVariants } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { drawerLabel, loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { canEdit, canManage, canMove, writableDrawers } from '#lib/permissions.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	type Document = components['schemas']['DocumentDetails'];
	type ReprocessStep = components['schemas']['ReprocessStep'];

	const id = $derived(page.params.id ?? '');
	let document = $state<Document | null>(null);
	let lookup = $state<Lookup | null>(null);
	let problem = $state<unknown>(null);
	let loadedSource = $state<string | null>(null);
	let confirmDelete = $state(false);
	let moveOpen = $state(false);
	let reprocessOpen = $state(false);
	let target = $state('');
	let fromStep = $state<ReprocessStep>('ocr');
	let busy = $state(false);

	const base = $derived(`/api/v1/documents/${id}`);
	const sources = $derived(
		document?.media_type === 'application/pdf'
			? [`${base}/archive`, `${base}/original`]
			: [`${base}/archive`]
	);
	const user = $derived(session.user);
	const editable = $derived(document !== null && canEdit(document, user));
	const manageable = $derived(document !== null && canManage(document, user));
	const movable = $derived(document !== null && canMove(document, user));
	const status = $derived(document?.processing.status);
	const drawers = $derived(
		writableDrawers(lookup?.drawers ?? [], user).filter(
			(drawer) => drawer.id !== document?.drawer_id
		)
	);
	const drawerName = $derived(
		lookup?.drawers.find((drawer) => drawer.id === document?.drawer_id)?.name ?? null
	);

	// Steps a reprocessing may start from: not past a failed step, not `file` while fields wait.
	const STEPS: ReprocessStep[] = [
		'ocr',
		'parse',
		'classify',
		'extract_fields',
		'apply_rules',
		'file'
	];
	const stepLabel: Record<ReprocessStep, () => string> = {
		ocr: m.step_ocr,
		parse: m.step_parse,
		classify: m.step_classify,
		extract_fields: m.step_extract_fields,
		apply_rules: m.step_apply_rules,
		file: m.step_file
	};
	const steps = $derived.by(() => {
		const failedAt =
			document?.processing.status === 'failed' ? document.processing.current_step : null;
		return STEPS.filter((step) => {
			if (document?.processing.status === 'review' && step === 'file') return false;
			return failedAt === null || STEPS.indexOf(step) <= STEPS.indexOf(failedAt as ReprocessStep);
		}).map((step) => ({ value: step, label: stepLabel[step]() }));
	});

	// An answer for a document the page has left behind must not replace the current one.
	async function load() {
		const wanted = id;
		try {
			const loaded = await unwrap(
				api.GET('/api/v1/documents/{id}', { params: { path: { id: wanted } } })
			);
			if (wanted === id) {
				document = loaded;
				problem = null;
			}
		} catch (error) {
			if (wanted === id) problem = error;
		}
	}

	$effect(() => {
		void id;
		untrack(() => {
			if (document && document.id !== id) document = null;
		});
	});

	$effect(() => {
		void id;
		void events.generation;
		void load();
	});

	onMount(() => {
		loadLookup().then((value) => (lookup = value), reportError);
		return events.subscribe((event) => {
			if (event.document_id !== id) return;
			if (event.type === 'document.deleted') void goto(`${BASE}/documents`);
			else void load();
		});
	});

	async function act(action: () => Promise<unknown>, done: string) {
		busy = true;
		try {
			await action();
			toast.success(done);
			await load();
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
			() => unwrap(api.POST('/api/v1/documents/{id}/retry', { params: { path: { id } } })),
			m.retry_started()
		);
	}

	async function reprocess() {
		if (
			await act(
				() =>
					unwrap(
						api.POST('/api/v1/documents/{id}/reprocess', {
							params: { path: { id } },
							body: { from_step: fromStep }
						})
					),
				m.reprocess_started()
			)
		)
			reprocessOpen = false;
	}

	async function move() {
		if (!target) return;
		if (
			await act(
				() =>
					unwrap(
						api.POST('/api/v1/documents/{id}/move', {
							params: { path: { id } },
							body: { drawer_id: target }
						})
					),
				m.move_done()
			)
		)
			moveOpen = false;
	}

	async function remove() {
		await unwrap(api.DELETE('/api/v1/documents/{id}', { params: { path: { id } } }));
		toast.success(m.delete_done());
		await goto(`${BASE}/documents`);
	}

	const notFound = $derived(problem instanceof ApiError && problem.status === 404);
</script>

<svelte:head><title>{document?.title ?? m.nav_documents()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/documents"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{m.nav_documents()}
	</a>

	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{notFound ? m.document_not_found() : describeError(problem)}</p>
			{#if !notFound}<Button variant="outline" onclick={load}>{m.retry()}</Button>{/if}
		</div>
	{:else if !document}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<header class="flex flex-wrap items-start justify-between gap-3">
			<div class="flex min-w-0 flex-col gap-2">
				<h1 class="text-[1.75rem] font-semibold tracking-tight break-words">{document.title}</h1>
				<p class="text-sm text-muted-foreground">
					{document.original_filename} · {formatDate(document.created_at)}
					{#if drawerName}
						· {drawerName}{/if}
				</p>
			</div>
			<LaneBadge lane={document.lane} />
		</header>

		{#if status === 'failed' || status === 'review'}
			<p class="rounded-xl border bg-card px-4 py-3 text-sm" role="status">
				{status === 'failed' ? m.processing_failed() : m.processing_review()}
			</p>
		{/if}

		<div class="flex flex-wrap gap-2">
			<a href="{base}/original" download class={cn(buttonVariants({ variant: 'outline' }))}>
				<Download aria-hidden="true" />
				{m.download_original()}
			</a>
			{#if loadedSource === `${base}/archive`}
				<a href="{base}/archive" download class={cn(buttonVariants({ variant: 'outline' }))}>
					<Download aria-hidden="true" />
					{m.download_archive()}
				</a>
			{/if}
			{#if movable}
				<Button
					variant="outline"
					onclick={() => {
						target = drawers[0]?.id ?? '';
						moveOpen = true;
					}}
					disabled={drawers.length === 0}
				>
					<FolderInput aria-hidden="true" />
					{m.move_title()}
				</Button>
			{/if}
			{#if manageable && status === 'failed'}
				<Button variant="outline" disabled={busy} onclick={retry}>
					<RotateCcw aria-hidden="true" />
					{m.retry_step()}
				</Button>
			{/if}
			{#if manageable && status !== 'processing' && steps.length > 0}
				<Button
					variant="outline"
					disabled={busy}
					onclick={() => {
						fromStep = steps[0].value;
						reprocessOpen = true;
					}}
				>
					<RefreshCw aria-hidden="true" />
					{m.reprocess_title()}
				</Button>
			{/if}
			{#if manageable}
				<Button variant="destructive" onclick={() => (confirmDelete = true)}>
					<Trash2 aria-hidden="true" />
					{m.delete_title()}
				</Button>
			{/if}
		</div>

		<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_24rem]">
			<div class="flex min-h-[32rem] flex-col lg:h-[calc(100vh-14rem)]">
				{#key id}
					<PdfViewer
						{sources}
						label={document.title}
						onsource={(source) => (loadedSource = source)}
					/>
				{/key}
			</div>
			<div class="flex flex-col gap-6">
				{#if editable && lookup}
					<section class="flex flex-col gap-3" aria-labelledby="edit-heading">
						<h2 id="edit-heading" class="text-lg font-semibold">{m.edit_title()}</h2>
						<DocumentEditor {document} {lookup} onsaved={(updated) => (document = updated)} />
					</section>
				{:else if !editable}
					<p class="text-sm text-muted-foreground">{m.edit_read_only()}</p>
				{/if}
			</div>
		</div>

		{#if manageable}
			<section class="flex flex-col gap-3" aria-labelledby="log-heading">
				<h2 id="log-heading" class="text-lg font-semibold">{m.log_title()}</h2>
				<ProcessingLog documentId={id} />
			</section>
		{/if}
	{/if}
</div>

<ConfirmDialog
	bind:open={confirmDelete}
	title={m.delete_title()}
	description={m.delete_confirm({ title: document?.title ?? '' })}
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
			<Field.Label for="move-drawer">{m.field_drawer()}</Field.Label>
			<NativeSelect
				id="move-drawer"
				bind:value={target}
				options={drawers.map((drawer) => ({
					value: drawer.id,
					label: drawerLabel(drawer, lookup?.users ?? [], user?.id)
				}))}
			/>
		</Field.Field>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (moveOpen = false)}>{m.cancel()}</Button>
			<Button disabled={busy || !target} onclick={move}>{m.move_title()}</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>

<Dialog.Root bind:open={reprocessOpen}>
	<Dialog.Content>
		<Dialog.Header>
			<Dialog.Title>{m.reprocess_title()}</Dialog.Title>
			<Dialog.Description>{m.reprocess_hint()}</Dialog.Description>
		</Dialog.Header>
		<Field.Field>
			<Field.Label for="reprocess-step">{m.reprocess_from()}</Field.Label>
			<NativeSelect id="reprocess-step" bind:value={fromStep} options={steps} />
		</Field.Field>
		<Dialog.Footer>
			<Button variant="ghost" onclick={() => (reprocessOpen = false)}>{m.cancel()}</Button>
			<Button disabled={busy} onclick={reprocess}>{m.reprocess_title()}</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
