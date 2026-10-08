<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import RuleBuilder from '#lib/components/rules/RuleBuilder.svelte';
	import { describeError } from '#lib/errors.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { drawersFor } from '#lib/permissions.ts';
	import { problemText } from '#lib/rules/describe.ts';
	import { saveErrors } from '#lib/rules/errors.ts';
	import { fromApi, toApi, validate, type RuleModel } from '#lib/rules/model.ts';
	import { changeSwitchedOff } from '#lib/rules/save.ts';
	import { session } from '#lib/session.svelte.ts';

	type Rule = components['schemas']['RuleOut'];

	const id = $derived(page.params.id ?? '');
	let rule = $state<Rule | null>(null);
	let model = $state<RuleModel | null>(null);
	let lookup = $state<Lookup | null>(null);
	let problem = $state<string | null>(null);
	let errors = $state<Record<string, string>>({});
	let general = $state<string | null>(null);
	let busy = $state(false);

	// A user rule files only where its owner may write, also when an admin changes it.
	const drawers = $derived.by(() => {
		const owner =
			rule?.owner_id === session.user?.id
				? session.user
				: (lookup?.users.find((user) => user.id === rule?.owner_id) ?? null);
		return drawersFor(lookup?.drawers ?? [], owner, session.user);
	});

	$effect(() => {
		const wanted = id;
		Promise.all([
			unwrap(api.GET('/api/v1/rules/{id}', { params: { path: { id: wanted } } })),
			lookup ?? loadLookup()
		]).then(
			([found, loaded]) => {
				if (wanted !== id) return;
				[rule, model, lookup, problem] = [found, fromApi(found), loaded, null];
			},
			(error) => {
				if (wanted === id) problem = describeError(error);
			}
		);
	});

	async function save() {
		if (!rule || !model || !lookup) return;
		const problems = validate(model, rule.scope, lookup.attributes);
		errors = Object.fromEntries(
			Object.entries(problems).map(([key, entry]) => [key, problemText(entry)])
		);
		general = Object.keys(errors).length > 0 ? m.rule_form_invalid() : null;
		if (general) return;
		const definition = toApi(model);
		const before = toApi(fromApi(rule));
		if (JSON.stringify(definition) === JSON.stringify(before)) {
			await goto(`${BASE}/rules/${rule.id}`);
			return;
		}
		busy = true;
		try {
			const saved = await changeSwitchedOff(rule, definition);
			toast.success(m.rule_saved());
			await goto(`${BASE}/rules/${saved.id}/apply?saved=1`);
		} catch (error) {
			({ byKey: errors, general } = saveErrors(error, model));
			if (!general && Object.keys(errors).length > 0) general = m.rule_form_invalid();
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{m.rule_edit_title({ name: rule?.name ?? '' })} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/rules/{id}"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{rule?.name ?? m.nav_rules()}
	</a>
	<h1 class="text-[1.75rem] font-semibold tracking-tight">
		{m.rule_edit_title({ name: rule?.name ?? '' })}
	</h1>

	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !rule || !model || !lookup}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<RuleBuilder
			{model}
			scope={rule.scope}
			{lookup}
			{drawers}
			{errors}
			{general}
			{busy}
			cancelHref="{BASE}/rules/{rule.id}"
			onsubmit={save}
		/>
	{/if}
</div>
