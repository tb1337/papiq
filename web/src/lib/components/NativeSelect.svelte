<script lang="ts">
	import { cn } from '#lib/utils.ts';

	export interface Option {
		value: string;
		label: string;
	}

	// A native select, styled like the inputs: reliable with keyboard, screen readers and phones.
	let {
		value = $bindable(''),
		options,
		id,
		class: className = '',
		disabled = false,
		'aria-label': ariaLabel,
		'aria-invalid': ariaInvalid,
		onchange
	}: {
		value?: string;
		options: readonly Option[];
		id?: string;
		class?: string;
		disabled?: boolean;
		'aria-label'?: string;
		'aria-invalid'?: boolean;
		/** Called with the new value when the user picks one. */
		onchange?: (value: string) => void;
	} = $props();
</script>

<select
	{id}
	bind:value
	{disabled}
	aria-label={ariaLabel}
	aria-invalid={ariaInvalid}
	onchange={(event) => onchange?.(event.currentTarget.value)}
	class={cn(
		'h-11 w-full min-w-0 rounded-xl border border-input bg-card px-3 text-base transition-colors focus-visible:border-ring disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive md:text-sm dark:bg-input/30',
		className
	)}
>
	{#each options as option (option.value)}
		<option value={option.value}>{option.label}</option>
	{/each}
</select>
