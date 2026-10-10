<script lang="ts">
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Input } from '#lib/components/ui/input/index.ts';
	import type { FieldDefinition } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import type { Value } from '#lib/rules/model.ts';

	// One value of a field in its JSON form: text, number and date as text, yes/no as a
	// boolean, an amount with its currency, a choice from the field's list. `textual` asks for
	// plain text whatever the type (`contains`, `matches`).
	let {
		field,
		value,
		onchange,
		label,
		id,
		invalid = false,
		textual = false
	}: {
		field: FieldDefinition | null;
		value: Value;
		onchange: (value: Value) => void;
		label: string;
		id?: string;
		invalid?: boolean;
		textual?: boolean;
	} = $props();

	const type = $derived(textual ? 'text' : (field?.data_type ?? 'text'));
	const text = $derived(
		typeof value === 'string' ? value : typeof value === 'number' ? String(value) : ''
	);
	// The date field shows only YYYY-MM-DD; other spellings the API accepts stay visible as text.
	const dateField = $derived(type === 'date' && (text === '' || /^\d{4}-\d{2}-\d{2}$/.test(text)));
	const money = $derived(
		typeof value === 'object' && value !== null && !Array.isArray(value)
			? value
			: { amount: '', currency: 'EUR' }
	);
	const choices = $derived([
		{ value: '', label: m.rule_choose() },
		...(field?.choices ?? []).map((choice) => ({ value: choice, label: choice })),
		...(text && !(field?.choices ?? []).includes(text)
			? [{ value: text, label: `${text} (${m.rule_unknown()})` }]
			: [])
	]);
</script>

{#if type === 'boolean'}
	<NativeSelect
		{id}
		aria-label={label}
		aria-invalid={invalid || undefined}
		value={value === false ? 'false' : 'true'}
		options={[
			{ value: 'true', label: m.value_yes() },
			{ value: 'false', label: m.value_no() }
		]}
		onchange={(next) => onchange(next === 'true')}
	/>
{:else if type === 'amount'}
	<div class="flex gap-2">
		<Input
			{id}
			aria-label={label}
			aria-invalid={invalid || undefined}
			inputmode="decimal"
			value={money.amount}
			oninput={(event) =>
				onchange({ ...money, amount: event.currentTarget.value.replace(',', '.') })}
			class="min-w-0 flex-1"
		/>
		<Input
			aria-label={m.field_currency()}
			maxlength={3}
			value={money.currency}
			oninput={(event) => onchange({ ...money, currency: event.currentTarget.value.toUpperCase() })}
			class="w-20 uppercase"
		/>
	</div>
{:else if type === 'choice'}
	<NativeSelect
		{id}
		aria-label={label}
		aria-invalid={invalid || undefined}
		value={text}
		options={choices}
		onchange={(next) => onchange(next)}
	/>
{:else}
	<Input
		{id}
		aria-label={label}
		aria-invalid={invalid || undefined}
		type={dateField ? 'date' : type === 'link' ? 'url' : 'text'}
		inputmode={type === 'number' ? 'decimal' : undefined}
		value={text}
		oninput={(event) =>
			onchange(
				type === 'number' ? event.currentTarget.value.replace(',', '.') : event.currentTarget.value
			)}
	/>
{/if}
