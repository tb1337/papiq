<script lang="ts">
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import MultiPicker from '#lib/components/MultiPicker.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import ValueInput from '#lib/components/rules/ValueInput.svelte';
	import TemplateInput from '#lib/components/templates/TemplateInput.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import type { Space, Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { ACTION_LABELS } from '#lib/rules/describe.ts';
	import { defaultValue, type ActionNode } from '#lib/rules/model.ts';
	import { tagOptions } from '#lib/tags.ts';

	// One action. `spaces` are the ones the rule's owner may file into.
	let {
		action,
		lookup,
		spaces,
		error,
		onremove,
		onup,
		ondown
	}: {
		action: ActionNode;
		lookup: Lookup;
		spaces: readonly Space[];
		error?: string;
		onremove: () => void;
		onup?: () => void;
		ondown?: () => void;
	} = $props();

	const invalid = $derived(Boolean(error));
	const label = $derived(ACTION_LABELS[action.type]());

	function options(list: readonly { id: string; name: string }[], value: string) {
		const known = value === '' || list.some((entry) => entry.id === value);
		return [
			{ value: '', label: m.rule_choose() },
			...list.map((entry) => ({ value: entry.id, label: entry.name })),
			...(known ? [] : [{ value, label: m.rule_unknown() }])
		];
	}

	function withUnknown(list: { value: string; label: string }[], value: string) {
		const known = value === '' || list.some((entry) => entry.value === value);
		return [
			{ value: '', label: m.rule_choose() },
			...list,
			...(known ? [] : [{ value, label: m.rule_unknown() }])
		];
	}

	const field = $derived(
		action.type === 'set_field'
			? (lookup.fields.find((entry) => entry.id === action.field_id) ?? null)
			: null
	);
</script>

<div class="flex flex-col gap-2 rounded-xl border bg-background p-3" data-key={action.key}>
	<div class="flex flex-wrap items-start gap-2">
		<span class="w-full pt-2 text-sm font-medium sm:w-44">{label}</span>
		<div class="flex min-w-0 flex-1 basis-64 flex-col gap-2">
			{#if action.type === 'set_space'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.space_id}
					options={options(spaces, action.space_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_contact'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.contact_id}
					options={options(lookup.contacts, action.contact_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_document_type'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.document_type_id}
					options={options(lookup.documentTypes, action.document_type_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_topic'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.topic_id}
					options={withUnknown(tagOptions(lookup.tags, 'topic'), action.topic_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_title'}
				<TemplateInput id="title-{action.key}" {label} {invalid} bind:value={action.template} />
			{:else if action.type === 'add_tags' || action.type === 'remove_tags'}
				<MultiPicker
					{label}
					{invalid}
					options={tagOptions(lookup.tags, 'document')}
					selected={action.tag_ids}
					onchange={(next) => {
						if (action.type === 'add_tags' || action.type === 'remove_tags') action.tag_ids = next;
					}}
				/>
			{:else if action.type === 'set_field'}
				<NativeSelect
					aria-label={m.rule_condition_field()}
					aria-invalid={(invalid && !action.field_id) || undefined}
					value={action.field_id}
					options={options(lookup.fields, action.field_id)}
					onchange={(id) => {
						if (action.type !== 'set_field') return;
						const chosen = lookup.fields.find((entry) => entry.id === id) ?? null;
						action.field_id = id;
						action.value = (defaultValue('field', 'is', chosen) ?? '') as typeof action.value;
					}}
					class="h-10"
				/>
				{#if action.field_id}
					<ValueInput
						{field}
						{invalid}
						template
						id="value-{action.key}"
						label={m.rule_condition_value()}
						value={action.value}
						onchange={(next) => {
							if (action.type === 'set_field') action.value = next as typeof action.value;
						}}
					/>
				{/if}
			{:else if action.type === 'force_review'}
				<TemplateInput
					id="reason-{action.key}"
					label={m.rule_review_reason()}
					{invalid}
					bind:value={action.reason}
				/>
			{/if}
		</div>
		<div class="flex gap-0.5">
			<Button
				variant="ghost"
				size="icon"
				aria-label={m.rule_move_up()}
				disabled={!onup}
				onclick={() => onup?.()}
			>
				<ArrowUp aria-hidden="true" />
			</Button>
			<Button
				variant="ghost"
				size="icon"
				aria-label={m.rule_move_down()}
				disabled={!ondown}
				onclick={() => ondown?.()}
			>
				<ArrowDown aria-hidden="true" />
			</Button>
			<Button variant="ghost" size="icon" aria-label={m.rule_remove()} onclick={onremove}>
				<Trash2 aria-hidden="true" />
			</Button>
		</div>
	</div>
	{#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
</div>
