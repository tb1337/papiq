<script lang="ts">
	import type { FieldDefinition } from '#lib/masterdata.svelte.ts';
	import type { InputValue } from '#lib/fields.ts';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { m } from '#lib/paraglide/messages.js';

	// The input for one field, by its data type. An empty field removes the value.
	let {
		field,
		value = $bindable(),
		error,
		id
	}: {
		field: FieldDefinition;
		// A review holds lists too (tags); a field never gets one.
		value: InputValue | string[];
		error?: string;
		id: string;
	} = $props();

	const yesNo = $derived([
		{ value: '', label: m.value_unset() },
		{ value: 'true', label: m.value_yes() },
		{ value: 'false', label: m.value_no() }
	]);
	const choices = $derived([
		{ value: '', label: m.value_unset() },
		...field.choices.map((choice) => ({ value: choice, label: choice }))
	]);
</script>

<Field.Field>
	<Field.Label for={id}>{field.name}</Field.Label>
	{#if field.data_type === 'amount' && typeof value === 'object' && !Array.isArray(value)}
		<div class="flex gap-2">
			<Input
				{id}
				inputmode="decimal"
				bind:value={value.amount}
				aria-invalid={error ? true : undefined}
				class="flex-1"
			/>
			<Input
				bind:value={value.currency}
				maxlength={3}
				aria-label={m.field_currency()}
				class="w-24 uppercase"
			/>
		</div>
	{:else if field.data_type === 'boolean' && typeof value === 'string'}
		<NativeSelect {id} bind:value options={yesNo} aria-invalid={error ? true : undefined} />
	{:else if field.data_type === 'choice' && typeof value === 'string'}
		<NativeSelect {id} bind:value options={choices} aria-invalid={error ? true : undefined} />
	{:else if typeof value === 'string'}
		<Input
			{id}
			bind:value
			type={field.data_type === 'date' ? 'date' : field.data_type === 'link' ? 'url' : 'text'}
			inputmode={field.data_type === 'number' ? 'decimal' : undefined}
			aria-invalid={error ? true : undefined}
		/>
	{/if}
	{#if error}<Field.Error>{error}</Field.Error>{/if}
</Field.Field>
