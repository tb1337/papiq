<script lang="ts">
	import FileText from '@lucide/svelte/icons/file-text';

	// The preview of the first page; none yet while the file is still being processed.
	let { id, class: className = '' }: { id: string; class?: string } = $props();
	let missing = $state(false);
</script>

{#if missing}
	<div class="grid place-items-center bg-secondary text-muted-foreground {className}">
		<FileText class="size-6" aria-hidden="true" />
	</div>
{:else}
	<img
		src="/api/v1/documents/{id}/preview"
		alt=""
		loading="lazy"
		onerror={() => (missing = true)}
		class="bg-white object-cover object-top {className}"
	/>
{/if}
