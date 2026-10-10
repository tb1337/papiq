<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { goto } from '$app/navigation';
	import { BASE } from '#lib/base.ts';
	import RuleBuilder from '#lib/components/rules/RuleBuilder.svelte';
	import { describeError } from '#lib/errors.ts';
	import { loadLookup, type Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { drawersFor } from '#lib/permissions.ts';
	import { problemText } from '#lib/rules/describe.ts';
	import { saveErrors } from '#lib/rules/errors.ts';
	import { newModel, toApi, validate, type Scope } from '#lib/rules/model.ts';
	import { createSwitchedOff } from '#lib/rules/save.ts';
	import { session } from '#lib/session.svelte.ts';

	const model = $state(newModel());
	let scope = $state<Scope>('user');
	let lookup = $state<Lookup | null>(null);
	let problem = $state<string | null>(null);
	let errors = $state<Record<string, string>>({});
	let general = $state<string | null>(null);
	let busy = $state(false);

	const drawers = $derived(drawersFor(lookup?.drawers ?? [], session.user, session.user));

	onMount(() => {
		loadLookup().then(
			(value) => (lookup = value),
			(error) => (problem = describeError(error))
		);
	});

	async function save() {
		if (!lookup) return;
		const problems = validate(model, scope, lookup.fields);
		errors = Object.fromEntries(
			Object.entries(problems).map(([key, entry]) => [key, problemText(entry)])
		);
		general = Object.keys(errors).length > 0 ? m.rule_form_invalid() : null;
		if (general) return;
		busy = true;
		try {
			const rule = await createSwitchedOff(scope, toApi(model));
			toast.success(m.rule_saved());
			await goto(`${BASE}/rules/${rule.id}/apply?saved=1`);
		} catch (error) {
			({ byKey: errors, general } = saveErrors(error, model));
			if (!general && Object.keys(errors).length > 0) general = m.rule_form_invalid();
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{m.rule_new_title()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/rules"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{m.nav_rules()}
	</a>
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.rule_new_title()}</h1>

	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !lookup}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<RuleBuilder
			{model}
			{scope}
			scopes={session.isAdmin ? ['user', 'global'] : []}
			onscope={(next) => (scope = next)}
			{lookup}
			{drawers}
			{errors}
			{general}
			{busy}
			cancelHref="{BASE}/rules"
			onsubmit={save}
		/>
	{/if}
</div>
