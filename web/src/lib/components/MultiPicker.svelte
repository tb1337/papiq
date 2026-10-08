<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	export interface PickerOption {
		value: string;
		label: string;
	}

	// Several values from a list: chosen ones as chips, the rest as checkboxes with a filter.
	// Chosen values missing from the list (deleted, no longer visible) stay until removed.
	let {
		options,
		selected,
		onchange,
		label,
		invalid = false,
		id
	}: {
		options: readonly PickerOption[];
		selected: readonly string[];
		onchange: (selected: string[]) => void;
		label: string;
		invalid?: boolean;
		id?: string;
	} = $props();

	let query = $state('');
	const labelOf = (value: string) =>
		options.find((option) => option.value === value)?.label ?? m.rule_unknown();
	// The API keeps repeated values of a list; they show as one chip and go together.
	const chips = $derived([...new Set(selected)]);
	const shown = $derived(
		options.filter((option) => option.label.toLowerCase().includes(query.trim().toLowerCase()))
	);

	function toggle(value: string, on: boolean) {
		onchange(on ? [...selected, value] : selected.filter((entry) => entry !== value));
	}
</script>

<div
	{id}
	role="group"
	aria-label={label}
	class={cn(
		'flex min-w-0 flex-col gap-2 rounded-xl border border-input bg-card p-2 dark:bg-input/30',
		invalid && 'border-destructive'
	)}
>
	<div class="flex flex-wrap gap-1.5">
		{#each chips as value (value)}
			<span class="inline-flex h-7 items-center gap-1 rounded-full bg-secondary pr-1 pl-3 text-sm">
				{labelOf(value)}
				<button
					type="button"
					class="grid size-5 place-items-center rounded-full hover:bg-background"
					aria-label={m.rule_remove_value({ name: labelOf(value) })}
					onclick={() => toggle(value, false)}
				>
					<X class="size-3" aria-hidden="true" />
				</button>
			</span>
		{:else}
			<span class="px-1 text-sm text-muted-foreground">{m.rule_nothing_chosen()}</span>
		{/each}
	</div>
	{#if options.length > 6}
		<Input
			bind:value={query}
			placeholder={m.rule_filter()}
			aria-label={m.rule_filter()}
			class="h-9"
		/>
	{/if}
	<ul class="flex max-h-44 flex-col overflow-y-auto">
		{#each shown as option (option.value)}
			<li>
				<label
					class="flex cursor-pointer items-center gap-2 rounded-lg px-2 py-1.5 text-sm hover:bg-secondary"
				>
					<input
						type="checkbox"
						class="size-4 accent-primary"
						checked={selected.includes(option.value)}
						onchange={(event) => toggle(option.value, event.currentTarget.checked)}
					/>
					{option.label}
				</label>
			</li>
		{/each}
	</ul>
</div>
