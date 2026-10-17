<script lang="ts">
	import Lightbulb from '@lucide/svelte/icons/lightbulb';
	import SearchX from '@lucide/svelte/icons/search-x';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import type { Snippet } from 'svelte';
	import { chosen } from '#lib/candidates.ts';
	import type { Candidate } from '#lib/candidates.ts';
	import ContactAvatar from '#lib/components/ContactAvatar.svelte';
	import CandidateList from '#lib/components/document/CandidateList.svelte';
	import SourceChip from '#lib/components/document/SourceChip.svelte';
	import SourcesInfo from '#lib/components/document/SourcesInfo.svelte';
	import { describeValue } from '#lib/describe.ts';
	import { byPage, provenance, type FieldEvidence, type Place } from '#lib/document-view.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import TypeIcon from '#lib/components/TypeIcon.svelte';
	import { cn } from '#lib/utils.ts';

	// One field of the document with what backs it. A field that waits for a decision is a
	// card with the confidence, reason and quote, and what the signals proposed (a click takes
	// one); one Papiq only suggests without making anyone wait (`advice`, the topic) is a calm
	// card with the reason and the candidates; any other is a label and the input, with the
	// page of its quote and where its value comes from. Hovering or focusing it marks the quote
	// in the viewer.
	let {
		key,
		label,
		inputId,
		evidence,
		lookup,
		review = false,
		advice = false,
		quoted = true,
		applies = true,
		active = false,
		required = false,
		byHand = false,
		hint = null,
		onfocus,
		onjump,
		onpick,
		children
	}: {
		key: string;
		label: string;
		inputId: string;
		evidence?: FieldEvidence;
		lookup: Lookup;
		/** The document is being reviewed: open fields are cards. */
		review?: boolean;
		/** A suggestion that waits for nobody: a calm card with the candidates. */
		advice?: boolean;
		/** Whether a quote could back it; one never quoted (the topic) tells no missing one. */
		quoted?: boolean;
		/** The checked value is still the field's (its quote's places still apply). */
		applies?: boolean;
		active?: boolean;
		/** The field must have a value (a required field of the document's type). */
		required?: boolean;
		/** Set by hand only: never read from the document, so it has no quote. */
		byHand?: boolean;
		/** What belongs in the field (its description), under its name. */
		hint?: string | null;
		onfocus: (key: string | null) => void;
		onjump: (key: string, place: Place) => void;
		/** Take a proposed value for the field. */
		onpick?: (key: string, value: unknown) => void;
		children: Snippet;
	} = $props();

	const check = $derived(evidence?.check);
	const card = $derived(review && evidence?.open === true);
	const calm = $derived(!card && advice && evidence !== undefined);
	const kind = $derived(check ? provenance(check) : null);
	// One chip per page; beyond `CHIPS` pages the rest behind "+N".
	const CHIPS = 6;
	const places = $derived(byPage(applies ? (check?.locations ?? []) : []));
	let allPages = $state(false);
	const shownPlaces = $derived(allPages ? places : places.slice(0, CHIPS));
	const hiddenPlaces = $derived(places.length - shownPlaces.length);
	const labelId = $derived(`${inputId}-label`);
	const hintId = $derived(`${inputId}-hint`);
	const sources = $derived(check && applies ? chosen(check) : []);
	const pick = $derived(onpick ? (value: unknown) => onpick(key, value) : undefined);
</script>

<!-- Before a proposed contact its logo or initials, before a proposed type its icon. -->
{#snippet lead(candidate: Candidate)}
	{#if key === 'contact'}
		{@const found = lookup.contacts.find((entry) => entry.id === candidate.value)}
		{#if found}<ContactAvatar contact={found} size="xs" />{/if}
	{:else if key === 'document_type'}
		{@const found = lookup.documentTypes.find((entry) => entry.id === candidate.value)}
		<TypeIcon icon={found?.icon} class="size-3.5 text-muted-foreground" />
	{/if}
{/snippet}

{#snippet labelText()}
	{label}{#if required}<span class="text-destructive" aria-hidden="true">&nbsp;*</span><span
			class="sr-only">({m.field_required()})</span
		>{/if}
{/snippet}

{#snippet chips()}
	{#each shownPlaces as place (place.page)}
		<SourceChip {place} onjump={(picked) => onjump(key, picked)} />
	{/each}
	{#if hiddenPlaces > 0}
		<button
			type="button"
			onclick={() => (allPages = true)}
			aria-label={m.source_more({ count: hiddenPlaces })}
			class="shrink-0 rounded-full bg-panel px-2 font-mono text-[11px] leading-5 text-brand hover:underline"
		>
			+{hiddenPlaces}
		</button>
	{/if}
{/snippet}

<div
	role="group"
	aria-labelledby={labelId}
	aria-describedby={hint ? hintId : undefined}
	data-field={key}
	onmouseenter={() => onfocus(key)}
	onmouseleave={() => onfocus(null)}
	onfocusin={() => onfocus(key)}
	onfocusout={() => onfocus(null)}
	class={cn(
		card
			? 'my-1 flex flex-col gap-2.5 rounded-2xl border border-lane-yellow-foreground/25 bg-lane-yellow/25 p-3.5 transition-shadow'
			: calm
				? 'my-1 flex flex-col gap-2.5 rounded-2xl border bg-card p-3.5 transition-shadow'
				: '-mx-2.5 flex flex-col gap-1.5 rounded-xl px-2.5 py-2 transition-colors',
		active && (card || calm ? 'ring-2 ring-primary' : 'bg-panel')
	)}
>
	{#if calm && check}
		<div class="flex items-center gap-2">
			<Lightbulb class="size-4 shrink-0 text-brand" aria-hidden="true" strokeWidth={2.2} />
			<label id={labelId} for={inputId} class="font-semibold">{@render labelText()}</label>
		</div>
		{#if check.reason}<p class="text-sm">{check.reason}</p>{/if}
		{#if check.candidates?.length}
			<CandidateList candidates={check.candidates} onpick={pick} {lead} />
		{/if}
	{:else if card && check}
		<div class="flex items-center gap-2">
			<TriangleAlert
				class="size-4 shrink-0 text-lane-yellow-foreground"
				aria-hidden="true"
				strokeWidth={2.2}
			/>
			<label id={labelId} for={inputId} class="font-semibold">{@render labelText()}</label>
		</div>
		{#if check.reason}<p class="text-sm">{check.reason}</p>{/if}
		{#if check.candidates?.length}
			<CandidateList candidates={check.candidates} onpick={pick} {lead} />
		{/if}
		{#if check.new_name}
			<p class="text-sm">
				<span class="text-muted-foreground">{m.review_new_name()}:</span>
				{check.new_name}
			</p>
		{:else if (check.suggestion ?? null) === null && (check.proposed ?? null) !== null}
			<p class="text-sm">
				<span class="text-muted-foreground">{m.review_proposed()}:</span>
				{describeValue(check.proposed, lookup)}
			</p>
		{/if}
		{#if byHand}
			<p class="text-sm text-muted-foreground">{m.review_by_hand()}</p>
		{:else if !quoted}
			<!-- Never quoted (the topic): no missing quote to tell. -->
		{:else if kind === 'none'}
			<p
				class="flex items-center gap-2 rounded-lg bg-muted px-2.5 py-2 text-sm text-muted-foreground"
			>
				<SearchX class="size-4 shrink-0" aria-hidden="true" />
				{m.source_none()}
			</p>
		{:else}
			<!-- The quote on its own line, its pages below: many pages never squeeze it. -->
			<div
				class={cn(
					'flex flex-col gap-1.5 rounded-lg px-2.5 py-2 text-sm',
					kind === 'derived' ? 'border border-dashed border-input' : 'bg-muted'
				)}
			>
				<span class="flex items-baseline gap-2">
					<span class="shrink-0 text-muted-foreground">
						{kind === 'derived' ? m.source_derived() : m.review_evidence()}
					</span>
					<q class="min-w-0 flex-1 break-words italic">{check.evidence}</q>
				</span>
				{#if places.length > 0}
					<span class="flex flex-wrap gap-1">{@render chips()}</span>
				{/if}
			</div>
		{/if}
	{:else}
		<div class="flex items-center gap-2">
			<label id={labelId} for={inputId} class="text-sm font-medium text-muted-foreground">
				{@render labelText()}
			</label>
			<span class="ml-auto flex flex-wrap items-center justify-end gap-1">
				{#if sources.length > 0}
					<SourcesInfo candidates={sources} id="{inputId}-sources" />
				{/if}
				{#if places.length > 0}
					{@render chips()}
				{:else if kind === 'none' && quoted}
					<span class="text-xs text-muted-foreground">{m.source_without()}</span>
				{/if}
			</span>
		</div>
	{/if}
	{#if hint}<p id={hintId} class="-mt-0.5 text-xs text-muted-foreground">{hint}</p>{/if}
	{@render children()}
</div>
