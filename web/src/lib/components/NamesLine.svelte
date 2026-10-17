<script lang="ts">
	import ContactAvatar from '#lib/components/ContactAvatar.svelte';
	import GroupChips from '#lib/components/GroupChips.svelte';
	import TypeIcon from '#lib/components/TypeIcon.svelte';
	import type { Tag } from '#lib/masterdata.svelte.ts';
	import { cn } from '#lib/utils.ts';

	// A document's contact and type by name, the contact with its logo or initials (`avatar`),
	// the type with its icon, each with its shown groups (outlined chips), and a date, apart by
	// "·": a line of a card or a row, inside its link.
	let {
		parts,
		date = null,
		tags,
		class: className
	}: {
		parts: readonly {
			name: string | null | undefined;
			groupIds?: readonly string[];
			avatar?: { name: string; logo?: { url: string } | null } | null;
			icon?: string | null;
		}[];
		date?: string | null;
		tags: readonly Tag[];
		class?: string;
	} = $props();
	const named = $derived(parts.filter((part) => part.name));
</script>

{#if named.length > 0 || date}
	<span class={cn('flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1', className)}>
		{#each named as part, index (index)}
			{#if index > 0}<span aria-hidden="true">·</span>{/if}
			<span class="flex min-w-0 items-center gap-1.5">
				{#if part.avatar}<ContactAvatar contact={part.avatar} size="xs" />{/if}
				<TypeIcon icon={part.icon} class="size-3.5" />
				<span class="min-w-0 truncate">{part.name}</span>
			</span>
			<GroupChips {tags} ids={part.groupIds ?? []} interactive={false} />
		{/each}
		{#if date}
			{#if named.length > 0}<span aria-hidden="true">·</span>{/if}
			<span class="whitespace-nowrap">{date}</span>
		{/if}
	</span>
{/if}
