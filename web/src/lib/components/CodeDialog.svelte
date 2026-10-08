<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { m } from '#lib/paraglide/messages.js';

	// A dialog that asks for one code from the authenticator (or a recovery code). The API decides
	// whether it is right; its answer shows under the field.
	let {
		open = $bindable(false),
		title,
		description,
		submitLabel,
		destructive = false,
		onsubmit
	}: {
		open?: boolean;
		title: string;
		description: string;
		submitLabel: string;
		destructive?: boolean;
		onsubmit: (code: string) => Promise<void>;
	} = $props();

	let code = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	$effect(() => {
		if (open) {
			code = '';
			error = null;
		}
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		error = null;
		try {
			await onsubmit(code.replace(/\s+/g, ''));
			open = false;
		} catch (problem) {
			error = describeError(problem);
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content>
		<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
			<Dialog.Header>
				<Dialog.Title>{title}</Dialog.Title>
				<Dialog.Description>{description}</Dialog.Description>
			</Dialog.Header>
			<Field.Field>
				<Field.Label for="code-dialog-input">{m.totp_code()}</Field.Label>
				<Input
					id="code-dialog-input"
					bind:value={code}
					inputmode="numeric"
					autocomplete="one-time-code"
					maxlength={64}
					required
					aria-invalid={error ? true : undefined}
				/>
				{#if error}<Field.Error>{error}</Field.Error>{/if}
			</Field.Field>
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (open = false)}>{m.cancel()}</Button>
				<Button
					type="submit"
					variant={destructive ? 'destructive' : 'default'}
					disabled={busy || code.trim() === ''}
				>
					{submitLabel}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
