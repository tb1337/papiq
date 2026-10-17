<script lang="ts">
	import LucideIcon from '#lib/components/LucideIcon.svelte';

	// The icon of a document type, if it has one.
	let { icon, class: className = 'size-4' }: { icon: string | null | undefined; class?: string } =
		$props();
</script>

{#if icon}<LucideIcon name={icon} class={className} />{/if}
