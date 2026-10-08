<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import RuleView from '#lib/components/rules/RuleView.svelte';
	import { describeError } from '#lib/errors.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';

	// One saved version of a rule, to read (decision E4: no restoring).
	const id = $derived(page.params.id ?? '');
	const number = $derived(Number(page.params.number ?? '0'));
	let version = $state<components['schemas']['RuleVersionOut'] | null>(null);
	let lookup = $state<Lookup | null>(null);
	let problem = $state<string | null>(null);

	$effect(() => {
		const [wantedId, wantedNumber] = [id, number];
		version = null;
		Promise.all([
			unwrap(
				api.GET('/api/v1/rules/{id}/versions/{number}', {
					params: { path: { id: wantedId, number: wantedNumber } }
				})
			),
			lookup ?? loadLookup()
		]).then(
			([found, loaded]) => {
				if (wantedId !== id || wantedNumber !== number) return;
				[version, lookup, problem] = [found, loaded, null];
			},
			(error) => {
				if (wantedId === id && wantedNumber === number) problem = describeError(error);
			}
		);
	});

	const author = $derived(
		lookup?.users.find((user) => user.id === version?.created_by)?.username ?? m.rule_unknown()
	);
</script>

<svelte:head>
	<title>{m.rule_version_n({ number })} · p:api:q</title>
</svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/rules/{id}"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{m.rule_versions()}
	</a>

	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !version}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<div class="flex flex-col gap-1">
			<h1 class="text-[1.75rem] font-semibold tracking-tight break-words">
				{m.rule_version_title({ name: version.name, number: version.version })}
			</h1>
			<span class="text-sm text-muted-foreground">
				{m.rule_version_meta({
					date: formatDate(version.created_at, { dateStyle: 'medium', timeStyle: 'short' }),
					user: author
				})} · {m.rule_priority_short({ priority: version.priority })}
			</span>
		</div>
		<p class="text-sm text-muted-foreground">{m.rule_version_hint()}</p>
		<section class="rounded-2xl border bg-card p-5">
			<RuleView definition={version} {lookup} />
		</section>
	{/if}
</div>
