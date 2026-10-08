<script lang="ts">
	import { cn } from '#lib/utils.ts';

	let { class: className = '' }: { class?: string } = $props();
</script>

<!-- The wordmark p:a~p~i:q; the struck p exists only in the logo. -->
<span class={cn('font-mono font-medium tracking-tight', className)} aria-label="p:api:q" role="img"
	>p:a<span class="text-brand line-through decoration-[0.09em]">p</span>i:q</span
>
