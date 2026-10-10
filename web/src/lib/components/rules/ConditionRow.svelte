<script lang="ts">
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import X from '@lucide/svelte/icons/x';
	import MultiPicker from '#lib/components/MultiPicker.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import ValueInput from '#lib/components/rules/ValueInput.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { CHANNEL_LABELS, fieldLabel, operatorLabel } from '#lib/rules/describe.ts';
	import {
		CHANNELS,
		CONDITION_FIELDS,
		changeField,
		changeConditionField,
		changeOperator,
		operatorsFor,
		takesNoValue,
		type ConditionField,
		type ConditionNode,
		type Operator
	} from '#lib/rules/model.ts';

	// One condition: condition field (and the field), comparison and the value it needs.
	let {
		condition,
		lookup,
		error,
		onremove,
		onup,
		ondown
	}: {
		condition: ConditionNode;
		lookup: Lookup;
		error?: string;
		onremove: () => void;
		onup?: () => void;
		ondown?: () => void;
	} = $props();

	const field = $derived(lookup.fields.find((entry) => entry.id === condition.fieldId) ?? null);
	const operators = $derived(operatorsFor(condition, lookup.fields));
	const conditionFieldOptions = CONDITION_FIELDS.map((field) => ({
		value: field,
		label: fieldLabel(field)
	}));
	const operatorOptions = $derived(
		operators.map((op) => ({
			value: op,
			label: operatorLabel(condition.field, op, field?.data_type ?? null)
		}))
	);
	const fieldOptions = $derived([
		{ value: '', label: m.rule_choose() },
		...lookup.fields.map((entry) => ({ value: entry.id, label: entry.name })),
		...(condition.fieldId && !field ? [{ value: condition.fieldId, label: m.rule_unknown() }] : [])
	]);
	/** The choices of an id field or the channel. */
	const choices = $derived.by(() => {
		switch (condition.field) {
			case 'contact':
				return lookup.contacts.map((entry) => ({ value: entry.id, label: entry.name }));
			case 'document_type':
				return lookup.documentTypes.map((entry) => ({ value: entry.id, label: entry.name }));
			case 'tags':
				return lookup.tags.map((entry) => ({ value: entry.id, label: entry.name }));
			case 'channel':
				return CHANNELS.map((channel) => ({ value: channel, label: CHANNEL_LABELS[channel]() }));
			case 'field':
				return field?.data_type === 'choice'
					? field.choices.map((choice) => ({ value: choice, label: choice }))
					: null;
			default:
				return null;
		}
	});
	const single = $derived(typeof condition.value === 'string' ? condition.value : '');
	const list = $derived(Array.isArray(condition.value) ? condition.value : []);
	const textual = $derived(
		condition.field === 'text' || condition.op === 'contains' || condition.op === 'matches'
	);
	const valueLabel = m.rule_condition_value();
	const invalid = $derived(Boolean(error));

	function withUnknown(options: { value: string; label: string }[], value: string) {
		const known = value === '' || options.some((option) => option.value === value);
		return [
			{ value: '', label: m.rule_choose() },
			...options,
			...(known ? [] : [{ value, label: m.rule_unknown() }])
		];
	}
</script>

<div class="flex flex-col gap-2 rounded-xl border bg-background p-3" data-key={condition.key}>
	<div class="flex flex-wrap items-start gap-2">
		<NativeSelect
			aria-label={m.rule_condition_subject()}
			value={condition.field}
			options={conditionFieldOptions}
			onchange={(field) => changeConditionField(condition, field as ConditionField, lookup.fields)}
			class="h-10 w-full sm:w-44"
		/>
		{#if condition.field === 'field'}
			<NativeSelect
				aria-label={m.rule_condition_field()}
				aria-invalid={invalid && !condition.fieldId}
				value={condition.fieldId ?? ''}
				options={fieldOptions}
				onchange={(id) => id && changeField(condition, id, lookup.fields)}
				class="h-10 w-full sm:w-44"
			/>
		{/if}
		<NativeSelect
			aria-label={m.rule_condition_operator()}
			value={condition.op}
			options={operatorOptions}
			onchange={(op) => changeOperator(condition, op as Operator, lookup.fields)}
			class="h-10 w-full sm:w-48"
		/>
		<div class="min-w-0 flex-1 basis-56">
			{#if takesNoValue(condition.op)}
				<span class="sr-only">{m.rule_no_value()}</span>
			{:else if condition.op === 'in' && choices}
				<MultiPicker
					label={valueLabel}
					{invalid}
					options={choices}
					selected={list}
					onchange={(next) => (condition.value = next)}
				/>
			{:else if condition.op === 'in'}
				<div class="flex flex-col gap-2">
					{#each list as entry, index (index)}
						<div class="flex gap-1">
							<Input
								aria-label={valueLabel}
								aria-invalid={invalid || undefined}
								value={entry}
								oninput={(event) =>
									(condition.value = list.map((item, at) =>
										at === index ? event.currentTarget.value : item
									))}
							/>
							<Button
								variant="ghost"
								size="icon"
								aria-label={m.rule_remove_value({ name: entry })}
								onclick={() => (condition.value = list.filter((_, at) => at !== index))}
							>
								<X aria-hidden="true" />
							</Button>
						</div>
					{/each}
					<Button
						variant="outline"
						size="sm"
						class="w-fit"
						onclick={() => (condition.value = [...list, ''])}
					>
						{m.rule_add_value()}
					</Button>
				</div>
			{:else if choices && condition.field !== 'field'}
				<NativeSelect
					aria-label={valueLabel}
					aria-invalid={invalid || undefined}
					value={single}
					options={withUnknown(choices, single)}
					onchange={(next) => (condition.value = next)}
					class="h-10"
				/>
			{:else if condition.field === 'document_date'}
				<Input
					type={single === '' || /^\d{4}-\d{2}-\d{2}$/.test(single) ? 'date' : 'text'}
					aria-label={valueLabel}
					aria-invalid={invalid || undefined}
					value={single}
					oninput={(event) => (condition.value = event.currentTarget.value)}
				/>
			{:else if condition.field === 'field'}
				<ValueInput
					{field}
					{textual}
					{invalid}
					label={valueLabel}
					value={condition.value}
					onchange={(next) => (condition.value = next)}
				/>
			{:else}
				<Input
					aria-label={valueLabel}
					aria-invalid={invalid || undefined}
					value={single}
					oninput={(event) => (condition.value = event.currentTarget.value)}
				/>
			{/if}
			{#if condition.op === 'matches'}
				<label class="mt-2 flex items-center gap-2 text-sm">
					<input
						type="checkbox"
						class="size-4 accent-primary"
						bind:checked={condition.caseSensitive}
					/>
					{m.rule_case_sensitive()}
				</label>
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
