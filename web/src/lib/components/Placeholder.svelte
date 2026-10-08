<script lang="ts">
	import Construction from '@lucide/svelte/icons/construction';
	import { m } from '#lib/paraglide/messages.js';

	// An area that the next milestone fills.
	let { title }: { title: string } = $props();
</script>

<svelte:head><title>{title} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{title}</h1>
	<div
		class="flex flex-col items-center gap-3 rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
	>
		<Construction class="size-8" aria-hidden="true" />
		<p>{m.placeholder_text()}</p>
	</div>
</div>
