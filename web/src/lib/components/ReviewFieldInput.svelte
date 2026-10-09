<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import type { components } from '#lib/api/schema.ts';
	import FieldValueInput from '#lib/components/FieldValueInput.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeValue, fieldLabel } from '#lib/describe.ts';
	import { formatNumber } from '#lib/i18n.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { fieldId, type ReviewValue } from '#lib/review.ts';

	// One open field of a review: what the model proposed and how sure the checks are, and the
	// input for the decision. An empty input means "no value".
	let {
		check,
		lookup,
		value = $bindable(),
		id
	}: {
		check: components['schemas']['FieldCheckOut'];
		lookup: Lookup;
		value: ReviewValue;
		id: string;
	} = $props();

	const field = $derived(lookup.fields.find((entry) => entry.id === fieldId(check.field)));
	const options = (list: readonly { id: string; name: string }[]) => [
		{ value: '', label: m.value_unset() },
		...list.map((entry) => ({ value: entry.id, label: entry.name }))
	];
	const tagName = $derived(new Map(lookup.tags.map((tag) => [tag.id, tag.name])));
	const tags = $derived(Array.isArray(value) ? value : []);
	const freeTags = $derived(lookup.tags.filter((tag) => !tags.includes(tag.id)));
</script>

<section class="flex flex-col gap-3 rounded-2xl border bg-card p-4" aria-labelledby="{id}-label">
	<div class="flex flex-wrap items-baseline justify-between gap-2">
		<h3 id="{id}-label" class="font-medium">{fieldLabel(check.field, lookup)}</h3>
		<span class="text-sm text-muted-foreground">
			{m.review_confidence({
				confidence: formatNumber(check.confidence, { style: 'percent', maximumFractionDigits: 0 })
			})}
		</span>
	</div>
	{#if check.reason}<p class="text-sm">{check.reason}</p>{/if}
	<dl class="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 text-sm text-muted-foreground">
		<dt>{m.review_proposed()}</dt>
		<dd class="text-foreground">{describeValue(check.proposed, lookup)}</dd>
		{#if check.evidence}
			<dt>{m.review_evidence()}</dt>
			<dd class="text-foreground italic">„{check.evidence}“</dd>
		{/if}
		{#if check.new_name}
			<dt>{m.review_new_name()}</dt>
			<dd class="text-foreground">{check.new_name}</dd>
		{/if}
	</dl>

	{#if field}
		<FieldValueInput {field} bind:value id="{id}-input" />
	{:else if check.field === 'contact' && typeof value === 'string'}
		<NativeSelect
			id="{id}-input"
			aria-label={m.field_contact()}
			bind:value
			options={options(lookup.contacts)}
		/>
	{:else if check.field === 'document_type' && typeof value === 'string'}
		<NativeSelect
			id="{id}-input"
			aria-label={m.field_document_type()}
			bind:value
			options={options(lookup.documentTypes)}
		/>
	{:else if check.field === 'document_date' && typeof value === 'string'}
		<Field.Field>
			<Input id="{id}-input" type="date" bind:value aria-label={m.field_date()} />
		</Field.Field>
	{:else if check.field === 'tags' && Array.isArray(value)}
		<div class="flex flex-wrap gap-2">
			{#each tags as tag (tag)}
				<span
					class="inline-flex h-8 items-center gap-1 rounded-full bg-secondary pr-1 pl-3 text-sm"
				>
					{tagName.get(tag) ?? tag}
					<button
						type="button"
						class="grid size-6 place-items-center rounded-full hover:bg-background"
						aria-label={m.filter_tag_remove({ name: tagName.get(tag) ?? tag })}
						onclick={() => (value = tags.filter((entry) => entry !== tag))}
					>
						<X class="size-3.5" aria-hidden="true" />
					</button>
				</span>
			{/each}
		</div>
		{#key tags.length}
			<NativeSelect
				id="{id}-input"
				aria-label={m.field_tag_add()}
				value=""
				options={options(freeTags).map((option, index) =>
					index === 0 ? { ...option, label: m.field_tag_add() } : option
				)}
				onchange={(picked) => picked && (value = [...tags, picked])}
			/>
		{/key}
	{/if}
</section>
