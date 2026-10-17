<script lang="ts">
	import TagChip from '#lib/components/TagChip.svelte';
	import type { Tag } from '#lib/masterdata.svelte.ts';
	import { shownChips } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// The shown groups of a contact or type (`ids`), outlined chips next to it; they come with
	// the contact or type and are not removed at the document. `interactive`: the path on a
	// tap too (not inside a link).
	let {
		tags,
		ids,
		interactive = true,
		class: className
	}: {
		tags: readonly Tag[];
		ids: readonly string[];
		interactive?: boolean;
		class?: string;
	} = $props();
	const groups = $derived(shownChips(tags, ids));
</script>

{#if groups.length > 0}
	<span class={cn('inline-flex max-w-full flex-wrap gap-1', className)}>
		{#each groups as tag (tag.id)}<TagChip {tag} group {interactive} size="sm" />{/each}
	</span>
{/if}
