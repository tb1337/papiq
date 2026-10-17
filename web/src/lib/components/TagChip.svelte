<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import LucideIcon from '#lib/components/LucideIcon.svelte';
	import * as Popover from '#lib/components/ui/popover/index.ts';
	import { chipStyle, isHex } from '#lib/colors.ts';
	import type { Tag } from '#lib/masterdata.svelte.ts';
	import { pathLabel } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// A tag as a chip: its icon and own name in its colour, the path as tooltip and, where
	// `interactive`, on a tap (a phone has no hover); a group outlined, a topic square (tags are
	// round); `onremove` adds a button that removes it. On a phone both take a finger's tap
	// beyond their edges (44 pixels high; the button also to its right, never over the name).
	let {
		tag,
		group = false,
		topic = false,
		interactive = false,
		size = 'md',
		onremove,
		removeLabel = ''
	}: {
		tag: Pick<Tag, 'id' | 'name' | 'path'> & Partial<Pick<Tag, 'icon' | 'color'>>;
		group?: boolean;
		topic?: boolean;
		interactive?: boolean;
		size?: 'sm' | 'md';
		onremove?: () => void;
		removeLabel?: string;
	} = $props();

	const path = $derived(pathLabel(tag));
	const nested = $derived(tag.path.length > 1);
	const style = $derived(chipStyle(tag.color));
	const coloured = $derived(style !== null);
</script>

<span
	class={cn(
		'inline-flex max-w-full min-w-0 items-center gap-1',
		topic ? 'rounded-md border' : 'rounded-full',
		!coloured &&
			(topic ? 'border-primary/25 bg-primary/10' : group ? 'border bg-background' : 'bg-secondary'),
		coloured &&
			(group
				? 'border border-(--chip-fg)/45 bg-background text-(--chip-fg)'
				: cn('bg-(--chip-bg) font-medium text-(--chip-fg)', topic && 'border-(--chip-fg)/25')),
		isHex(tag.color) && 'chip-own',
		// An own colour may be close to the card (white in light, black in dark): a faint ring
		// keeps the chip's shape.
		isHex(tag.color) && !group && !topic && 'ring-1 ring-(--chip-fg)/15 ring-inset',
		size === 'sm' ? 'h-6 px-2 text-xs' : 'h-8 pl-3 text-sm',
		size === 'md' && (onremove ? 'pr-1' : 'pr-3')
	)}
	{style}
	title={path}
	data-color={tag.color ?? undefined}
>
	{#if tag.icon}
		<LucideIcon name={tag.icon} class={size === 'sm' ? 'size-3' : 'size-3.5'} />
	{/if}
	{#if interactive && nested}
		<Popover.Root>
			<Popover.Trigger
				class={cn(
					'min-w-0 truncate hover:underline',
					topic ? 'rounded-sm' : 'rounded-full',
					size === 'sm' ? 'max-sm:-my-3.5 max-sm:py-3.5' : 'max-sm:-my-3 max-sm:py-3'
				)}
				aria-label={path}
			>
				{tag.name}
			</Popover.Trigger>
			<Popover.Content class="w-auto max-w-72 px-3 py-2 text-sm" side="top">
				{path}
			</Popover.Content>
		</Popover.Root>
	{:else}
		<span class="min-w-0 truncate">{tag.name}</span>
	{/if}
	{#if onremove}
		<button
			type="button"
			class="relative grid size-6 shrink-0 place-items-center rounded-full after:absolute after:-inset-y-2.5 after:-right-2.5 after:left-0 hover:bg-background"
			aria-label={removeLabel}
			onclick={onremove}
		>
			<X class="size-3.5" aria-hidden="true" />
		</button>
	{/if}
</span>
