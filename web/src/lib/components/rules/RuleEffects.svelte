<script lang="ts">
	import type { components } from '#lib/api/schema.ts';
	import { describeValue, fieldLabel } from '#lib/describe.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';

	// What a rule does to a document: field old → new, then its notes (skipped, conflicts, …).
	let {
		effects,
		notes = [],
		lookup
	}: {
		effects: readonly components['schemas']['RuleEffectOut'][];
		notes?: readonly components['schemas']['RuleNoteOut'][];
		lookup: Lookup | null;
	} = $props();
</script>

<ul class="flex flex-col gap-0.5">
	{#each effects as effect (effect.field)}
		<li>
			{fieldLabel(effect.field, lookup)}:
			{describeValue(effect.old, lookup)} → {describeValue(effect.new, lookup)}
		</li>
	{/each}
	{#each notes as note (note.field + note.kind)}
		<li class="text-muted-foreground">{fieldLabel(note.field, lookup)}: {note.reason}</li>
	{/each}
</ul>
