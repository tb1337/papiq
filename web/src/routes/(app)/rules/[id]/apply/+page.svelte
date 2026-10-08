<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { onDestroy, untrack } from 'svelte';
	import { SvelteSet } from 'svelte/reactivity';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import RuleEffects from '#lib/components/rules/RuleEffects.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Progress } from '#lib/components/ui/progress/index.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { canChangeRule } from '#lib/permissions.ts';
	import {
		applyRequest,
		follow,
		hasConflict,
		preselected,
		type Application,
		type PreviewItem
	} from '#lib/rules/apply.ts';
	import { session } from '#lib/session.svelte.ts';

	type Rule = components['schemas']['RuleOut'];
	const PAGE = 50;

	const id = $derived(page.params.id ?? '');
	/** Right after saving (decision E2): switch on, switch on and apply, or leave off. */
	const saved = $derived(page.url.searchParams.get('saved') === '1');

	let rule = $state<Rule | null>(null);
	let lookup = $state<Lookup | null>(null);
	let problem = $state<string | null>(null);
	let items = $state<PreviewItem[]>([]);
	let version = $state<number | null>(null);
	let cursor = $state<string | null>(null);
	let complete = $state(false);
	let loading = $state(false);
	let loadingAll = $state(false);
	let previewError = $state<string | null>(null);
	const chosen = new SvelteSet<string>();
	const accepted = new SvelteSet<string>();
	let busy = $state(false);
	let application = $state<Application | null>(null);
	const stop = new AbortController();
	onDestroy(() => stop.abort());

	const request = $derived(
		version === null ? null : applyRequest(version, items, chosen, accepted)
	);
	const count = $derived(request?.document_ids.length ?? 0);
	const titles = $derived(new Map(items.map((item) => [item.document_id, item.title])));
	const plain = $derived(items.filter((item) => !hasConflict(item)));

	$effect(() => {
		const wanted = id;
		Promise.all([
			unwrap(api.GET('/api/v1/rules/{id}', { params: { path: { id: wanted } } })),
			untrack(() => lookup) ?? loadLookup()
		]).then(
			([found, loaded]) => {
				if (wanted !== id) return;
				[rule, lookup, problem] = [found, loaded, null];
				restart();
				void more();
			},
			(error) => {
				if (wanted === id) problem = describeError(error);
			}
		);
	});

	function restart() {
		items = [];
		version = null;
		cursor = null;
		complete = false;
		chosen.clear();
		accepted.clear();
	}

	/** The next page with at least one document, or the end. Pages may be empty midway. */
	async function more(): Promise<void> {
		if (loading || complete) return;
		loading = true;
		previewError = null;
		try {
			do {
				const preview = await unwrap(
					api.POST('/api/v1/rules/{id}/apply/preview', {
						params: { path: { id } },
						body: { cursor, limit: PAGE }
					})
				);
				if (version !== null && preview.version !== version) {
					// The rule changed between pages: what was loaded no longer holds.
					restart();
					toast.info(m.rule_preview_restarted());
					continue;
				}
				version = preview.version;
				items = [...items, ...preview.items];
				for (const documentId of preselected(preview.items)) chosen.add(documentId);
				cursor = preview.next_cursor;
				complete = cursor === null;
				if (preview.items.length > 0) break;
			} while (!complete && !stop.signal.aborted);
		} catch (error) {
			previewError = describeError(error);
		} finally {
			loading = false;
		}
	}

	async function all() {
		loadingAll = true;
		while (loadingAll && !complete && !previewError && !stop.signal.aborted) await more();
		loadingAll = false;
	}

	async function setEnabled(enabled: boolean) {
		rule = await unwrap(
			api.PATCH('/api/v1/rules/{id}', { params: { path: { id } }, body: { enabled } })
		);
	}

	async function apply(switchOn: boolean) {
		if (!request) return;
		busy = true;
		try {
			if (switchOn) await setEnabled(true);
			const started = await unwrap(
				api.POST('/api/v1/rules/{id}/apply', { params: { path: { id } }, body: request })
			);
			application = started;
			application = await follow(
				started,
				(applicationId) =>
					unwrap(
						api.GET('/api/v1/rule-applications/{id}', { params: { path: { id: applicationId } } })
					),
				(current) => (application = current),
				stop.signal
			);
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}

	async function switchOnOnly() {
		busy = true;
		try {
			await setEnabled(true);
			toast.success(m.rule_switched_on());
			await goto(`${BASE}/rules/${id}`);
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}

	function choose(item: PreviewItem, on: boolean) {
		const set = hasConflict(item) ? accepted : chosen;
		if (on) set.add(item.document_id);
		else set.delete(item.document_id);
	}
</script>

<svelte:head><title>{m.rule_apply_title({ name: rule?.name ?? '' })} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/rules/{id}"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{rule?.name ?? m.nav_rules()}
	</a>
	<h1 class="text-[1.75rem] font-semibold tracking-tight break-words">
		{m.rule_apply_title({ name: rule?.name ?? '' })}
	</h1>

	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !rule}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if application}
		{@const finished = application.status === 'done' || application.status === 'failed'}
		<section class="flex flex-col gap-4 rounded-2xl border bg-card p-5" aria-live="polite">
			{#if !finished}
				<p>{m.rule_apply_running({ done: application.done, total: application.total })}</p>
				<Progress value={application.done} max={Math.max(application.total, 1)} />
			{:else if application.status === 'failed'}
				<p class="text-destructive" role="alert">
					{m.rule_apply_failed({ error: application.error ?? '' })}
				</p>
			{:else}
				<p>
					{m.rule_apply_done({
						applied: application.applied,
						unchanged: application.unchanged,
						skipped: application.skipped.length
					})}
				</p>
			{/if}
			{#if application.skipped.length > 0}
				<div class="flex flex-col gap-1">
					<h2 class="font-medium">{m.rule_apply_skipped()}</h2>
					<ul class="flex flex-col gap-1 text-sm">
						{#each application.skipped as skip (skip.document_id)}
							<li>
								<a href="{BASE}/documents/{skip.document_id}" class="font-medium hover:underline">
									{titles.get(skip.document_id) ?? skip.document_id}
								</a>
								<span class="text-muted-foreground">{skip.reason}</span>
							</li>
						{/each}
					</ul>
				</div>
			{/if}
			{#if finished}
				<a href="{BASE}/rules/{id}" class="w-fit text-sm hover:underline">{rule.name}</a>
			{/if}
		</section>
	{:else}
		<p class="text-muted-foreground">{saved ? m.rule_saved_hint() : m.rule_apply_hint()}</p>

		{#if items.length > 0}
			<div class="flex flex-wrap items-center gap-2 text-sm">
				<span class="text-muted-foreground">
					{complete
						? m.rule_preview_complete({ count: items.length })
						: m.rule_preview_loaded({ count: items.length })}
				</span>
				<Button
					variant="ghost"
					size="sm"
					onclick={() => plain.forEach((item) => chosen.add(item.document_id))}
				>
					{m.rule_select_all()}
				</Button>
				<Button
					variant="ghost"
					size="sm"
					onclick={() => {
						chosen.clear();
						accepted.clear();
					}}
				>
					{m.rule_select_none()}
				</Button>
			</div>
			<ul class="flex flex-col gap-2">
				{#each items as item (item.document_id)}
					{@const conflict = hasConflict(item)}
					<li class="flex gap-3 rounded-2xl border bg-card p-4" class:border-lane-yellow={conflict}>
						{#if !conflict}
							<input
								type="checkbox"
								class="mt-1 size-4 shrink-0 accent-primary"
								aria-label={m.rule_select_document({ title: item.title })}
								checked={chosen.has(item.document_id)}
								onchange={(event) => choose(item, event.currentTarget.checked)}
							/>
						{:else}
							<TriangleAlert class="mt-0.5 size-4 shrink-0 text-lane-yellow" aria-hidden="true" />
						{/if}
						<div class="flex min-w-0 flex-1 flex-col gap-1.5 text-sm">
							<span class="flex flex-wrap items-center gap-2">
								<a
									href="{BASE}/documents/{item.document_id}"
									class="truncate text-base font-medium hover:underline">{item.title}</a
								>
								{#if conflict}<Badge variant="outline">{m.rule_conflict()}</Badge>{/if}
							</span>
							<RuleEffects effects={item.changes} notes={item.notes} {lookup} />
							{#if conflict}
								<RuleEffects effects={[]} notes={item.conflicts} {lookup} />
								<label class="flex w-fit items-center gap-2 font-medium">
									<input
										type="checkbox"
										class="size-4 accent-primary"
										checked={accepted.has(item.document_id)}
										onchange={(event) => choose(item, event.currentTarget.checked)}
									/>
									{m.rule_accept_conflict()}
								</label>
							{/if}
						</div>
					</li>
				{/each}
			</ul>
		{:else if complete}
			<p
				class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
			>
				{m.rule_preview_nothing()}
			</p>
		{/if}

		{#if previewError}
			<div class="flex flex-col items-start gap-3" role="alert">
				<p class="text-destructive">{previewError}</p>
				<Button variant="outline" onclick={() => void more()}>{m.retry()}</Button>
			</div>
		{:else if loading && items.length === 0}
			<p class="text-muted-foreground">{m.loading()}</p>
		{/if}

		{#if !complete && !previewError}
			<div class="flex flex-wrap justify-center gap-2">
				<Button variant="outline" disabled={loading} onclick={() => void more()}>
					{m.load_more()}
				</Button>
				{#if loadingAll}
					<Button variant="ghost" onclick={() => (loadingAll = false)}>
						{m.rule_preview_stop()}
					</Button>
				{:else}
					<Button variant="outline" disabled={loading} onclick={() => void all()}>
						{m.rule_preview_all()}
					</Button>
				{/if}
			</div>
		{/if}

		<div class="flex flex-wrap gap-2 border-t pt-4">
			{#if saved && canChangeRule(rule, session.user) && !rule.enabled}
				<Button disabled={busy} onclick={switchOnOnly}>{m.rule_switch_on_only()}</Button>
				<Button variant="outline" disabled={busy || count === 0} onclick={() => apply(true)}>
					{m.rule_switch_on_apply({ count })}
				</Button>
				<a
					href="{BASE}/rules/{id}"
					class="inline-flex h-9 items-center px-3 text-sm hover:underline"
				>
					{m.rule_leave_off()}
				</a>
			{:else}
				<Button disabled={busy || count === 0} onclick={() => apply(false)}>
					{m.rule_apply_selected({ count })}
				</Button>
			{/if}
		</div>
	{/if}
</div>
