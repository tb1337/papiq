<script lang="ts">
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Textarea } from '#lib/components/ui/textarea/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { type Extra, type Item, parseAliases, type Values } from '#lib/masterdata-api.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Create or change a contact (name, aliases), a document type (name, description) or a tag
	// (name). The API decides what is valid; its answer (e.g. 409 for a taken name) shows below.
	let {
		open = $bindable(false),
		title,
		extra,
		initial = null,
		submitLabel = m.save(),
		onsubmit
	}: {
		open?: boolean;
		title: string;
		extra: Extra;
		initial?: Item | null;
		submitLabel?: string;
		onsubmit: (values: Values) => Promise<void>;
	} = $props();

	let name = $state('');
	let aliases = $state('');
	let description = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	$effect(() => {
		if (open) {
			name = initial?.name ?? '';
			aliases = (initial?.aliases ?? []).join('\n');
			description = initial?.description ?? '';
			error = null;
		}
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		error = null;
		try {
			await onsubmit({
				name: name.trim(),
				aliases: parseAliases(aliases),
				description: description.trim() || null
			});
			open = false;
		} catch (problem) {
			error = describeError(problem);
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-lg">
		<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
			<Dialog.Header><Dialog.Title>{title}</Dialog.Title></Dialog.Header>
			<Field.Field>
				<Field.Label for="master-name">{m.field_name()}</Field.Label>
				<Input id="master-name" bind:value={name} maxlength={200} required />
			</Field.Field>
			{#if extra === 'aliases'}
				<Field.Field>
					<Field.Label for="master-aliases">{m.master_aliases()}</Field.Label>
					<Textarea id="master-aliases" bind:value={aliases} rows={4} />
					<Field.Description>{m.master_aliases_hint()}</Field.Description>
				</Field.Field>
			{:else if extra === 'description'}
				<Field.Field>
					<Field.Label for="master-description">{m.master_description()}</Field.Label>
					<Textarea id="master-description" bind:value={description} rows={3} maxlength={300} />
					<Field.Description>{m.master_description_hint()}</Field.Description>
				</Field.Field>
			{/if}
			{#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (open = false)}>{m.cancel()}</Button>
				<Button type="submit" disabled={busy || name.trim() === ''}>{submitLabel}</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
