<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import RotateCcw from '@lucide/svelte/icons/rotate-ccw';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { ApiError } from '#lib/api/problem.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import PdfViewer from '#lib/components/PdfViewer.svelte';
	import ReviewFieldInput from '#lib/components/ReviewFieldInput.svelte';
	import { Button, buttonVariants } from '#lib/components/ui/button/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { fieldLabel } from '#lib/describe.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { writableDrawers } from '#lib/permissions.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { buildConfirm, initialValue, isRuleField, type ReviewValue } from '#lib/review.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	type Review = components['schemas']['ReviewOut'];
	type Check = components['schemas']['FieldCheckOut'];

	const id = $derived(page.params.id ?? '');
	let review = $state<Review | null>(null);
	let lookup = $state<Lookup | null>(null);
	let problem = $state<unknown>(null);
	let values = $state<Record<string, ReviewValue>>({});
	let drawerId = $state('');
	let busy = $state(false);
	let formError = $state<string | null>(null);

	const base = $derived(`/api/v1/documents/${id}`);
	const checks = $derived<Check[]>(review ? review.open.flatMap((step) => step.fields) : []);
	const inputs = $derived(checks.filter((check) => !isRuleField(check.field)));
	const ruleNotes = $derived(
		checks.filter((check) => check.field === 'review' || check.field === 'title')
	);
	const hasDrawer = $derived(checks.some((check) => check.field === 'drawer'));
	const failedStep = $derived(review?.open.find((step) => step.outcome === 'failed') ?? null);
	const drawers = $derived(writableDrawers(lookup?.drawers ?? [], session.user));

	async function load() {
		try {
			review = await unwrap(api.GET('/api/v1/documents/{id}/review', { params: { path: { id } } }));
			problem = null;
		} catch (error) {
			problem = error;
		}
	}

	// The form starts from the document and the model's suggestions; a new state of the document
	// (after an event) starts it again.
	$effect(() => {
		if (!review || !lookup) return;
		const next: Record<string, ReviewValue> = {};
		for (const check of review.open.flatMap((step) => step.fields)) {
			next[check.field] = initialValue(check, review.document, lookup.attributes);
		}
		values = next;
		drawerId = review.document.drawer_id;
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
			if (event.type === 'document.deleted') void goto(`${BASE}/inbox`);
			else if (event.type !== 'document.step_completed') void load();
		});
	});

	async function confirm(event: SubmitEvent) {
		event.preventDefault();
		if (!review || !lookup || busy) return;
		busy = true;
		formError = null;
		try {
			await unwrap(
				api.POST('/api/v1/documents/{id}/confirm', {
					params: { path: { id } },
					body: buildConfirm(checks, values, review.document, lookup.attributes, drawerId)
				})
			);
			toast.success(m.review_confirmed());
			await goto(`${BASE}/inbox`);
		} catch (error) {
			formError =
				error instanceof ApiError && error.problem?.open_fields
					? m.review_open_fields({
							fields: error.problem.open_fields.map((field) => fieldLabel(field, lookup)).join(', ')
						})
					: describeError(error);
		} finally {
			busy = false;
		}
	}

	async function retry() {
		busy = true;
		try {
			await unwrap(api.POST('/api/v1/documents/{id}/retry', { params: { path: { id } } }));
			toast.success(m.retry_started());
			await goto(`${BASE}/inbox`);
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{review?.document.title ?? m.nav_inbox()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/inbox"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{m.nav_inbox()}
	</a>

	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">
				{problem instanceof ApiError && problem.status === 404
					? m.document_not_found()
					: describeError(problem)}
			</p>
		</div>
	{:else if !review || !lookup}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<header class="flex flex-wrap items-start justify-between gap-3">
			<div class="flex min-w-0 flex-col gap-2">
				<h1 class="text-[1.75rem] font-semibold tracking-tight break-words">
					{review.document.title}
				</h1>
				<a
					href="{BASE}/documents/{id}"
					class="w-fit text-sm text-muted-foreground underline-offset-4 hover:underline"
				>
					{m.review_open_document()}
				</a>
			</div>
			<LaneBadge lane={review.document.lane} />
		</header>

		<div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_26rem]">
			<div class="flex min-h-[32rem] flex-col lg:h-[calc(100vh-12rem)]">
				{#key id}
					<PdfViewer
						sources={[`${base}/archive`, `${base}/original`]}
						label={review.document.title}
					/>
				{/key}
			</div>

			<form class="flex flex-col gap-4" onsubmit={confirm} novalidate>
				{#if failedStep}
					<div class="flex flex-col gap-3 rounded-2xl border border-destructive/40 bg-card p-4">
						<p class="font-medium">{m.review_failed()}</p>
						{#if failedStep.reason}<p class="text-sm">{failedStep.reason}</p>{/if}
						<div class="flex flex-wrap gap-2">
							<Button type="button" variant="outline" disabled={busy} onclick={retry}>
								<RotateCcw aria-hidden="true" />
								{m.retry_step()}
							</Button>
							<a href="{BASE}/documents/{id}" class={cn(buttonVariants({ variant: 'ghost' }))}>
								{m.review_open_document()}
							</a>
						</div>
					</div>
				{/if}

				{#each ruleNotes as note (note.field)}
					<p class="rounded-xl border bg-card px-4 py-3 text-sm">
						<span class="font-medium">{fieldLabel(note.field, lookup)}:</span>
						{note.reason}
					</p>
				{/each}

				{#each inputs as check, index (check.field)}
					<ReviewFieldInput {check} {lookup} bind:value={values[check.field]} id="review-{index}" />
				{/each}

				{#if hasDrawer}
					<Field.Field>
						<Field.Label for="review-drawer">{m.field_drawer()}</Field.Label>
						<NativeSelect
							id="review-drawer"
							bind:value={drawerId}
							options={drawers.map((drawer) => ({ value: drawer.id, label: drawer.name }))}
						/>
					</Field.Field>
				{/if}

				{#if formError}<p class="text-sm text-destructive" role="alert">{formError}</p>{/if}

				{#if inputs.length === 0 && !failedStep && ruleNotes.length === 0 && !hasDrawer}
					<p class="text-muted-foreground">{m.review_nothing_open()}</p>
				{/if}

				{#if !failedStep || inputs.length > 0 || hasDrawer}
					<div>
						<Button type="submit" disabled={busy}>{m.review_confirm()}</Button>
					</div>
				{/if}
			</form>
		</div>
	{/if}
</div>
