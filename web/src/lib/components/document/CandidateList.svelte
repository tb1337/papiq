<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import type { Snippet } from 'svelte';
	import type { Candidate } from '#lib/candidates.ts';
	import CandidateSources from '#lib/components/document/CandidateSources.svelte';
	import { m } from '#lib/paraglide/messages.js';

	// The values the signals proposed for an open field, best first, with what each signal
	// said; one that exists can be taken with a click. A value another signal named against is
	// marked. `lead` goes before a value's name (a contact's logo, a type's icon).
	let {
		candidates,
		onpick,
		lead
	}: {
		candidates: readonly Candidate[];
		onpick?: (value: unknown) => void;
		lead?: Snippet<[Candidate]>;
	} = $props();
</script>

<div class="flex flex-col gap-1.5">
	<p class="text-xs font-medium text-muted-foreground">{m.candidates_title()}</p>
	<ul class="flex flex-col gap-1.5">
		{#each candidates as candidate, index (index)}
			<li
				class="flex flex-wrap items-center gap-x-2 gap-y-1 rounded-lg bg-card/70 px-2.5 py-1.5 text-sm"
			>
				{@render lead?.(candidate)}
				{#if candidate.value !== null && onpick}
					<button
						type="button"
						onclick={() => onpick(candidate.value)}
						aria-label={m.candidate_pick({ name: candidate.label })}
						class="font-medium text-brand hover:underline"
					>
						{candidate.label}
					</button>
				{:else}
					<span class="font-medium">{candidate.label}</span>
					{#if candidate.value === null}
						<span class="text-xs text-muted-foreground">{m.candidate_new()}</span>
					{/if}
				{/if}
				{#if candidate.contradicted}
					<span class="inline-flex items-center gap-1 text-xs text-lane-red-foreground">
						<CircleAlert class="size-3.5" aria-hidden="true" />
						{m.candidate_contradicted()}
					</span>
				{/if}
				<CandidateSources {candidate} />
			</li>
		{/each}
	</ul>
</div>
