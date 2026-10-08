<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { EVENT_TYPES, type EventType } from '#lib/events.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';

	type Webhook = components['schemas']['WebhookOut'];
	type Created = components['schemas']['WebhookCreated'];

	// Create or change a webhook. A new one answers with its secret, handed on to `oncreated`.
	let {
		open = $bindable(false),
		webhook = null,
		onsaved,
		oncreated
	}: {
		open?: boolean;
		webhook?: Webhook | null;
		onsaved: () => void;
		oncreated: (created: Created) => void;
	} = $props();

	let name = $state('');
	let url = $state('');
	let all = $state(true);
	let types = $state<string[]>([]);
	let active = $state(true);
	let error = $state<string | null>(null);
	let busy = $state(false);

	const labels: Record<EventType, () => string> = {
		'document.received': m.event_received,
		'document.step_completed': m.event_step_completed,
		'document.lane_changed': m.event_lane_changed,
		'document.filed': m.event_filed,
		'document.updated': m.event_updated,
		'document.deleted': m.event_deleted
	};

	$effect(() => {
		if (!open) return;
		error = null;
		name = webhook?.name ?? '';
		url = webhook?.url ?? '';
		all = webhook ? webhook.event_types.includes('*') : true;
		types = webhook ? webhook.event_types.filter((type) => type !== '*') : [];
		active = webhook?.active ?? true;
	});

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		error = null;
		const event_types = all ? ['*'] : types;
		try {
			if (webhook) {
				await unwrap(
					api.PATCH('/api/v1/webhooks/{id}', {
						params: { path: { id: webhook.id } },
						body: { name: name.trim(), url: url.trim(), event_types, active }
					})
				);
				toast.success(m.webhook_saved());
				onsaved();
			} else {
				const created = await unwrap(
					api.POST('/api/v1/webhooks', {
						body: { name: name.trim(), url: url.trim(), event_types, active }
					})
				);
				onsaved();
				oncreated(created);
			}
			open = false;
		} catch (failure) {
			error = describeError(failure);
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-lg">
		<form class="flex flex-col gap-4" onsubmit={submit} novalidate>
			<Dialog.Header>
				<Dialog.Title>{webhook ? m.webhook_edit() : m.webhook_new()}</Dialog.Title>
			</Dialog.Header>
			<Field.Field>
				<Field.Label for="wh-name">{m.field_name()}</Field.Label>
				<Input id="wh-name" bind:value={name} maxlength={100} required />
			</Field.Field>
			<Field.Field>
				<Field.Label for="wh-url">{m.webhook_url()}</Field.Label>
				<Input id="wh-url" type="url" bind:value={url} maxlength={2000} required />
				<Field.Description>{m.webhook_url_hint()}</Field.Description>
			</Field.Field>
			<fieldset class="flex flex-col gap-2">
				<legend class="mb-1 text-sm font-medium">{m.webhook_events()}</legend>
				<label class="flex items-center gap-2 text-sm">
					<input type="checkbox" bind:checked={all} class="size-4 accent-primary" />
					{m.webhook_events_all()}
				</label>
				{#if !all}
					{#each EVENT_TYPES as type (type)}
						<label class="flex items-center gap-2 pl-6 text-sm">
							<input
								type="checkbox"
								value={type}
								bind:group={types}
								class="size-4 accent-primary"
							/>
							{labels[type]()}
						</label>
					{/each}
				{/if}
			</fieldset>
			<label class="flex items-center gap-2 text-sm">
				<input type="checkbox" bind:checked={active} class="size-4 accent-primary" />
				{m.webhook_active()}
			</label>
			{#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (open = false)}>{m.cancel()}</Button>
				<Button
					type="submit"
					disabled={busy || name.trim() === '' || url.trim() === '' || (!all && types.length === 0)}
				>
					{webhook ? m.save() : m.create()}
				</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>
