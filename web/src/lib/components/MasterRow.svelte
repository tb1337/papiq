<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Pencil from '@lucide/svelte/icons/pencil';
	import type { Snippet } from 'svelte';
	import UsageCount, { type Unit, usageLabel } from '#lib/components/UsageCount.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { formatNumber } from '#lib/i18n.ts';
	import { phone } from '#lib/phone.svelte.ts';
	import { cn } from '#lib/utils.ts';

	export interface Usage {
		count: number | null | undefined;
		href: string;
		unit?: Unit;
	}

	// A row of a master data list. On a phone the whole row is a button that opens the editor:
	// its usage as a bare number (the last of `usage`) and a chevron, no other buttons. Wider,
	// the usage links to where it is listed and a pencil opens the editor. `before` stands
	// outside the button (a tree's chevron), `lead` before the content (avatar, icon).
	let {
		onopen,
		editLabel,
		usage = [],
		before,
		lead,
		children,
		class: className
	}: {
		onopen: () => void;
		editLabel: string;
		usage?: Usage[];
		before?: Snippet;
		lead?: Snippet;
		children: Snippet;
		class?: string;
	} = $props();

	const last = $derived(usage.at(-1));
</script>

{#if phone.current}
	<li class={cn('flex items-center', className)} data-row>
		{@render before?.()}
		<button
			type="button"
			class={cn(
				'flex min-h-14 min-w-0 flex-1 items-center gap-3 py-2.5 pr-3 text-left transition-colors hover:bg-panel focus-visible:bg-panel focus-visible:outline-none',
				before ? 'pl-1' : 'pl-4'
			)}
			onclick={onopen}
		>
			{@render lead?.()}
			<span class="flex min-w-0 flex-1 flex-col items-start gap-1">{@render children()}</span>
			{#if last && last.count !== null && last.count !== undefined}
				<span class="shrink-0 text-sm text-muted-foreground tabular-nums" aria-hidden="true">
					{formatNumber(last.count)}
				</span>
				<span class="sr-only">{usageLabel(last.count, last.unit)}</span>
			{/if}
			<ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
		</button>
	</li>
{:else}
	<li class={cn('flex items-center gap-3 py-2 pr-2', before ? 'pl-2' : 'pl-4', className)} data-row>
		{@render before?.()}
		{@render lead?.()}
		<div class="flex min-w-0 flex-1 flex-col items-start gap-1">{@render children()}</div>
		{#if usage.length > 0}
			<span class="flex shrink-0 items-center text-sm text-muted-foreground">
				{#each usage as entry, index (index)}
					{#if index > 0}<span aria-hidden="true">·</span>{/if}
					<UsageCount count={entry.count} href={entry.href} unit={entry.unit} />
				{/each}
			</span>
		{/if}
		<Button variant="ghost" size="icon" class="shrink-0" aria-label={editLabel} onclick={onopen}>
			<Pencil aria-hidden="true" />
		</Button>
	</li>
{/if}
