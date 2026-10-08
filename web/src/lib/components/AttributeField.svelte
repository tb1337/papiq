<script lang="ts">
	import type { Attribute } from '#lib/masterdata.svelte.ts';
	import type { FieldValue } from '#lib/attributes.ts';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { m } from '#lib/paraglide/messages.js';

	// The input for one attribute, by its data type. An empty field removes the value.
	let {
		attribute,
		value = $bindable(),
		error,
		id
	}: { attribute: Attribute; value: FieldValue; error?: string; id: string } = $props();

	const yesNo = $derived([
		{ value: '', label: m.value_unset() },
		{ value: 'true', label: m.value_yes() },
		{ value: 'false', label: m.value_no() }
	]);
	const choices = $derived([
		{ value: '', label: m.value_unset() },
		...attribute.choices.map((choice) => ({ value: choice, label: choice }))
	]);
</script>

<Field.Field>
	<Field.Label for={id}>{attribute.name}</Field.Label>
	{#if attribute.data_type === 'amount' && typeof value === 'object'}
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
				aria-label={m.attribute_currency()}
				class="w-24 uppercase"
			/>
		</div>
	{:else if attribute.data_type === 'boolean' && typeof value === 'string'}
		<NativeSelect {id} bind:value options={yesNo} aria-invalid={error ? true : undefined} />
	{:else if attribute.data_type === 'choice' && typeof value === 'string'}
		<NativeSelect {id} bind:value options={choices} aria-invalid={error ? true : undefined} />
	{:else if typeof value === 'string'}
		<Input
			{id}
			bind:value
			type={attribute.data_type === 'date'
				? 'date'
				: attribute.data_type === 'link'
					? 'url'
					: 'text'}
			inputmode={attribute.data_type === 'number' ? 'decimal' : undefined}
			aria-invalid={error ? true : undefined}
		/>
	{/if}
	{#if error}<Field.Error>{error}</Field.Error>{/if}
</Field.Field>
