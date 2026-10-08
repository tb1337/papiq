<script lang="ts">
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { TRIGGER_LABELS, describeAction, describeCondition } from '#lib/rules/describe.ts';
	import { fromApi, type ApiDefinition, type GroupNode } from '#lib/rules/model.ts';

	// A rule (or one of its versions) to read: when it runs, its conditions and actions.
	let { definition, lookup }: { definition: ApiDefinition; lookup: Lookup | null } = $props();

	const model = $derived(fromApi(definition));

	function heading(group: GroupNode): string {
		if (group.mode === 'all') return group.negate ? m.rule_group_not_all() : m.rule_group_all();
		return group.negate ? m.rule_group_not_any() : m.rule_group_any();
	}
</script>

{#snippet tree(group: GroupNode)}
	<div class="flex flex-col gap-1.5">
		<span class="text-sm text-muted-foreground">{heading(group)}</span>
		<ul class="flex flex-col gap-1.5 border-l-2 pl-3">
			{#each group.items as item (item.key)}
				<li>
					{#if item.kind === 'group'}
						{@render tree(item)}
					{:else}
						{describeCondition(item, lookup)}
					{/if}
				</li>
			{/each}
		</ul>
	</div>
{/snippet}

<dl class="grid gap-x-6 gap-y-4 sm:grid-cols-[10rem_1fr]">
	<dt class="text-sm font-medium text-muted-foreground">{m.rule_triggers()}</dt>
	<dd>{model.triggers.map((trigger) => TRIGGER_LABELS[trigger]()).join(' · ')}</dd>
	<dt class="text-sm font-medium text-muted-foreground">{m.rule_conditions()}</dt>
	<dd>{@render tree(model.conditions)}</dd>
	<dt class="text-sm font-medium text-muted-foreground">{m.rule_actions()}</dt>
	<dd>
		<ul class="flex flex-col gap-1.5">
			{#each model.actions as action (action.key)}
				<li>{describeAction(action, lookup)}</li>
			{/each}
		</ul>
	</dd>
</dl>
