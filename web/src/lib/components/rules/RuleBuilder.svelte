<script lang="ts">
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import ActionRow from '#lib/components/rules/ActionRow.svelte';
	import ConditionGroup from '#lib/components/rules/ConditionGroup.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import type { Drawer, Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { ACTION_LABELS, TRIGGER_LABELS } from '#lib/rules/describe.ts';
	import {
		LIMITS,
		TRIGGERS,
		actionTypesFor,
		move,
		newAction,
		type ActionType,
		type RuleModel,
		type Scope
	} from '#lib/rules/model.ts';

	// The form of a rule: name, priority, when it runs, its conditions and actions. The page
	// saves it; `errors` are by key (see `validate` and `saveErrors`).
	let {
		model,
		scope,
		scopes = [],
		onscope,
		lookup,
		drawers,
		errors,
		general,
		busy,
		cancelHref,
		onsubmit
	}: {
		model: RuleModel;
		scope: Scope;
		/** The scopes to choose from (admins, new rule); none: the scope stays. */
		scopes?: readonly Scope[];
		onscope?: (scope: Scope) => void;
		lookup: Lookup;
		drawers: readonly Drawer[];
		errors: Record<string, string>;
		general: string | null;
		busy: boolean;
		cancelHref: string;
		onsubmit: () => void;
	} = $props();

	const scopeLabels: Record<Scope, () => string> = {
		user: m.rule_scope_user,
		global: m.rule_scope_global
	};
	const addable = $derived([
		{ value: '', label: m.rule_add_action() },
		...actionTypesFor(scope).map((type) => ({ value: type, label: ACTION_LABELS[type]() }))
	]);
	let adding = $state('');

	function add(type: string) {
		if (type) model.actions.push(newAction(type as ActionType));
		adding = '';
	}

	function toggleTrigger(trigger: (typeof TRIGGERS)[number], on: boolean) {
		model.triggers = on
			? [...model.triggers, trigger]
			: model.triggers.filter((entry) => entry !== trigger);
	}
</script>

<form
	class="flex flex-col gap-6"
	novalidate
	onsubmit={(event) => {
		event.preventDefault();
		onsubmit();
	}}
>
	<div class="grid gap-4 sm:grid-cols-[1fr_12rem]">
		<Field.Field>
			<Field.Label for="rule-name">{m.field_name()}</Field.Label>
			<Input
				id="rule-name"
				bind:value={model.name}
				maxlength={LIMITS.name}
				aria-invalid={errors.name ? true : undefined}
			/>
			{#if errors.name}<Field.Error>{errors.name}</Field.Error>{/if}
		</Field.Field>
		<Field.Field>
			<Field.Label for="rule-priority">{m.rule_priority()}</Field.Label>
			<Input
				id="rule-priority"
				type="number"
				min={LIMITS.priorityMin}
				max={LIMITS.priorityMax}
				step={1}
				bind:value={model.priority}
				aria-invalid={errors.priority ? true : undefined}
			/>
			<Field.Description>{m.rule_priority_hint()}</Field.Description>
			{#if errors.priority}<Field.Error>{errors.priority}</Field.Error>{/if}
		</Field.Field>
	</div>

	{#if scopes.length > 1}
		<Field.Field>
			<Field.Label for="rule-scope">{m.rule_scope()}</Field.Label>
			<NativeSelect
				id="rule-scope"
				value={scope}
				options={scopes.map((entry) => ({ value: entry, label: scopeLabels[entry]() }))}
				onchange={(value) => onscope?.(value as Scope)}
			/>
		</Field.Field>
	{/if}

	<fieldset class="flex flex-col gap-2">
		<legend class="mb-2 text-sm font-medium">{m.rule_triggers()}</legend>
		{#each TRIGGERS as trigger (trigger)}
			<label class="flex items-center gap-2 text-sm">
				<input
					type="checkbox"
					class="size-4 accent-primary"
					checked={model.triggers.includes(trigger)}
					onchange={(event) => toggleTrigger(trigger, event.currentTarget.checked)}
				/>
				{TRIGGER_LABELS[trigger]()}
			</label>
		{/each}
		{#if errors.triggers}<p class="text-sm text-destructive" role="alert">{errors.triggers}</p>{/if}
	</fieldset>

	<section class="flex flex-col gap-3" aria-labelledby="rule-conditions">
		<h2 id="rule-conditions" class="text-lg font-semibold">{m.rule_conditions()}</h2>
		<ConditionGroup group={model.conditions} {model} {lookup} {errors} />
	</section>

	<section class="flex flex-col gap-3" aria-labelledby="rule-actions">
		<h2 id="rule-actions" class="text-lg font-semibold">{m.rule_actions()}</h2>
		{#each model.actions as action, index (action.key)}
			<ActionRow
				{action}
				{lookup}
				{drawers}
				error={errors[action.key]}
				onremove={() => model.actions.splice(index, 1)}
				onup={index > 0 ? () => move(model.actions, index, -1) : undefined}
				ondown={index < model.actions.length - 1 ? () => move(model.actions, index, 1) : undefined}
			/>
		{/each}
		<NativeSelect
			aria-label={m.rule_add_action()}
			bind:value={adding}
			options={addable}
			disabled={model.actions.length >= LIMITS.actions}
			onchange={add}
			class="h-10 w-full sm:w-72"
		/>
		{#if errors.actions}<p class="text-sm text-destructive" role="alert">{errors.actions}</p>{/if}
	</section>

	<div class="flex flex-col gap-3 border-t pt-4">
		{#if general}<p class="text-sm text-destructive" role="alert">{general}</p>{/if}
		<p class="text-sm text-muted-foreground">{m.rule_save_hint()}</p>
		<div class="flex flex-wrap gap-2">
			<Button type="submit" disabled={busy}>{m.rule_save()}</Button>
			<a href={cancelHref} class="inline-flex h-9 items-center px-3 text-sm hover:underline">
				{m.cancel()}
			</a>
		</div>
	</div>
</form>
