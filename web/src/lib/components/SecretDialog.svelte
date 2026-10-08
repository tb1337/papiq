<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';
	import type { Snippet } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Shows a secret exactly once (API token, webhook secret, recovery codes). The secret lives
	// only in the page's memory: closing the dialog drops it, nothing is stored.
	let {
		secret,
		title,
		description,
		onclose,
		extra
	}: {
		secret: string | null;
		title: string;
		description: string;
		onclose: () => void;
		extra?: Snippet;
	} = $props();

	let copied = $state(false);

	async function copy() {
		if (!secret) return;
		try {
			await navigator.clipboard.writeText(secret);
			copied = true;
		} catch {
			// No clipboard in an insecure page: the text stays selectable.
			copied = false;
		}
	}
</script>

<Dialog.Root bind:open={() => secret !== null, (open) => !open && onclose()}>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{title}</Dialog.Title>
			<Dialog.Description>{description}</Dialog.Description>
		</Dialog.Header>
		{#if secret}
			<pre
				class="rounded-xl border bg-secondary p-3 font-mono text-sm break-all whitespace-pre-wrap select-all"
				data-testid="secret">{secret}</pre>
			{@render extra?.()}
			<Dialog.Footer>
				<Button variant="outline" onclick={copy}>
					{#if copied}<Check aria-hidden="true" />{:else}<Copy aria-hidden="true" />{/if}
					{copied ? m.secret_copied() : m.secret_copy()}
				</Button>
				<Button onclick={onclose}>{m.secret_done()}</Button>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
