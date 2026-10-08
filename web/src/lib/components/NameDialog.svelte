<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { m } from '#lib/paraglide/messages.js';

	// A dialog with one name field: create or rename. The API decides what a valid name is; its
	// answer (e.g. 409 for a taken name) shows under the field.
	let {
		open = $bindable(false),
		title,
		label = m.field_name(),
		initial = '',
		submitLabel = m.save(),
		onsubmit
	}: {
		open?: boolean;
		title: string;
		label?: string;
		initial?: string;
		submitLabel?: string;
		onsubmit: (name: string) => Promise<void>;
	} = $props();

	let name = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	$effect(() => {
		if (open) {
			name = initial;
			error = null;
		}
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		error = null;
		try {
			await onsubmit(name.trim());
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
			<Dialog.Header><Dialog.Title>{title}</Dialog.Title></Dialog.Header>
			<Field.Field>
				<Field.Label for="name-dialog-input">{label}</Field.Label>
				<Input
					id="name-dialog-input"
					bind:value={name}
					maxlength={200}
					required
					aria-invalid={error ? true : undefined}
				/>
				{#if error}<Field.Error>{error}</Field.Error>{/if}
			</Field.Field>
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (open = false)}>{m.cancel()}</Button>
				<Button type="submit" disabled={busy || name.trim() === ''}>{submitLabel}</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
