<script lang="ts">
	import Plus from '@lucide/svelte/icons/plus';
	import { m } from '#lib/paraglide/messages.js';

	// What a picker offers an admin for a search that finds no tag of its kind named so: create
	// it at once ("“…” anlegen") or with its details in the tag dialog.
	let {
		name,
		busy = false,
		onquick,
		ondetails
	}: { name: string; busy?: boolean; onquick: () => void; ondetails: () => void } = $props();
</script>

<ul class="shrink-0 overflow-hidden rounded-2xl bg-panel text-brand">
	<li>
		<button
			type="button"
			class="flex min-h-12 w-full items-center gap-2.5 px-3.5 text-left font-medium hover:bg-brand/10 disabled:opacity-60"
			disabled={busy}
			onclick={onquick}
		>
			<Plus class="size-4 shrink-0" aria-hidden="true" />
			<span class="min-w-0 break-words">{m.tag_create_quick({ name })}</span>
		</button>
	</li>
	<li class="border-t border-brand/15">
		<button
			type="button"
			class="flex min-h-11 w-full items-center py-2 pr-3.5 pl-[2.375rem] text-left text-sm hover:bg-brand/10 disabled:opacity-60"
			disabled={busy}
			onclick={ondetails}
		>
			{m.tag_create_details()}
		</button>
	</li>
</ul>
