<script lang="ts">
	import * as AlertDialog from '#lib/components/ui/alert-dialog/index.ts';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import { reportError } from '#lib/errors.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	// Asks before a destructive action. The action runs on confirm; the dialog closes when it
	// succeeds and stays open (with the error as a toast) when it fails.
	let {
		open = $bindable(false),
		title,
		description,
		confirmLabel,
		destructive = true,
		onconfirm
	}: {
		open?: boolean;
		title: string;
		description: string;
		confirmLabel: string;
		destructive?: boolean;
		onconfirm: () => Promise<void>;
	} = $props();

	let busy = $state(false);

	async function confirm(event: Event) {
		event.preventDefault();
		busy = true;
		try {
			await onconfirm();
			open = false;
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}
</script>

<AlertDialog.Root bind:open>
	<AlertDialog.Content>
		<AlertDialog.Header>
			<AlertDialog.Title>{title}</AlertDialog.Title>
			<AlertDialog.Description>{description}</AlertDialog.Description>
		</AlertDialog.Header>
		<AlertDialog.Footer>
			<AlertDialog.Cancel disabled={busy}>{m.cancel()}</AlertDialog.Cancel>
			<AlertDialog.Action
				disabled={busy}
				onclick={confirm}
				class={cn(destructive && buttonVariants({ variant: 'destructive' }))}
			>
				{confirmLabel}
			</AlertDialog.Action>
		</AlertDialog.Footer>
	</AlertDialog.Content>
</AlertDialog.Root>
