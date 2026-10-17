<script lang="ts">
	import { tick } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import LucideIcon from '#lib/components/LucideIcon.svelte';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	// An icon, or none: the chosen one and, opened in place, the icons offered first by areas of
	// life and a search over every Lucide icon (`IconPickerPanel`, loaded when it opens: the
	// index of all icons and their words stay off the pages until then). Choosing one closes it
	// and gives the focus back to the button that opened it.
	let {
		value = $bindable(null),
		id
	}: {
		value?: string | null;
		id: string;
	} = $props();

	let open = $state(false);
	let toggle = $state<HTMLButtonElement | null>(null);
	const panel = () => import('#lib/components/IconPickerPanel.svelte');

	function choose(name: string | null) {
		value = name;
		open = false;
	}

	async function chosen(name: string) {
		choose(name);
		await tick();
		toggle?.focus();
	}
</script>

<div class="flex flex-col gap-2" role="group" aria-labelledby="{id}-label">
	<div class="flex flex-wrap items-center gap-2">
		<span
			class="grid size-10 shrink-0 place-items-center rounded-xl border bg-card"
			aria-hidden="true"
		>
			{#if value}<LucideIcon name={value} class="size-5" />{/if}
		</span>
		<span
			id="{id}-value"
			class={cn('min-w-0 flex-1 truncate text-sm', value ? 'font-mono' : 'text-muted-foreground')}
		>
			{value ?? m.icon_none()}
		</span>
		<Button
			bind:ref={toggle}
			variant="outline"
			size="sm"
			class="max-sm:h-11"
			aria-expanded={open}
			aria-controls={open ? `${id}-panel` : undefined}
			aria-describedby="{id}-value"
			onclick={() => (open = !open)}
		>
			{open ? m.icon_done() : m.icon_choose()}
		</Button>
		{#if value}
			<Button variant="ghost" size="sm" class="max-sm:h-11" onclick={() => choose(null)}>
				{m.icon_remove()}
			</Button>
		{/if}
	</div>
	{#if open}
		{#await panel() then { default: Panel }}
			<Panel {id} {value} onchoose={chosen} />
		{:catch}
			<p id="{id}-panel" class="text-sm text-destructive" role="alert">{m.icon_load_failed()}</p>
		{/await}
	{/if}
</div>
