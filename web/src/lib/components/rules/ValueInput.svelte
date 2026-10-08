<script lang="ts">
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Input } from '#lib/components/ui/input/index.ts';
	import type { Attribute } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import type { Value } from '#lib/rules/model.ts';

	// One value of an attribute in its JSON form: text, number and date as text, yes/no as a
	// boolean, an amount with its currency, a choice from the attribute's list. `textual` asks for
	// plain text whatever the type (`contains`, `matches`).
	let {
		attribute,
		value,
		onchange,
		label,
		id,
		invalid = false,
		textual = false
	}: {
		attribute: Attribute | null;
		value: Value;
		onchange: (value: Value) => void;
		label: string;
		id?: string;
		invalid?: boolean;
		textual?: boolean;
	} = $props();

	const type = $derived(textual ? 'text' : (attribute?.data_type ?? 'text'));
	const text = $derived(typeof value === 'string' ? value : '');
	const money = $derived(
		typeof value === 'object' && value !== null && !Array.isArray(value)
			? value
			: { amount: '', currency: 'EUR' }
	);
	const choices = $derived([
		{ value: '', label: m.rule_choose() },
		...(attribute?.choices ?? []).map((choice) => ({ value: choice, label: choice })),
		...(text && !(attribute?.choices ?? []).includes(text)
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
			aria-label={m.attribute_currency()}
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
		type={type === 'date' ? 'date' : type === 'link' ? 'url' : 'text'}
		inputmode={type === 'number' ? 'decimal' : undefined}
		value={text}
		oninput={(event) =>
			onchange(
				type === 'number' ? event.currentTarget.value.replace(',', '.') : event.currentTarget.value
			)}
	/>
{/if}
