<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Checkbox } from '#lib/components/ui/checkbox/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Label } from '#lib/components/ui/label/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError, fieldErrors } from '#lib/errors.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';

	// Changing the password ends every session and starts a new one for this browser.
	let current = $state('');
	let next = $state('');
	let revoke = $state(false);
	let errors = $state<Record<string, string>>({});
	let problem = $state<string | null>(null);
	let busy = $state(false);

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		errors = {};
		problem = null;
		try {
			const started = await unwrap(
				api.POST('/api/v1/auth/password', {
					body: { current_password: current, new_password: next, revoke_tokens: revoke }
				})
			);
			session.start(started);
			current = next = '';
			revoke = false;
			toast.success(m.password_changed());
		} catch (error) {
			errors = fieldErrors(error);
			if (Object.keys(errors).length === 0) problem = describeError(error);
		} finally {
			busy = false;
		}
	}
</script>

<Card.Root>
	<Card.Header>
		<Card.Title>{m.password_title()}</Card.Title>
		<Card.Description>{m.password_hint()}</Card.Description>
	</Card.Header>
	<Card.Content>
		<form class="flex max-w-md flex-col gap-4" onsubmit={submit} novalidate>
			<Field.Field>
				<Field.Label for="pw-current">{m.password_current()}</Field.Label>
				<Input
					id="pw-current"
					type="password"
					autocomplete="current-password"
					bind:value={current}
					required
					aria-invalid={errors.current_password ? true : undefined}
				/>
				{#if errors.current_password}<Field.Error>{errors.current_password}</Field.Error>{/if}
			</Field.Field>
			<Field.Field>
				<Field.Label for="pw-new">{m.password_new()}</Field.Label>
				<Input
					id="pw-new"
					type="password"
					autocomplete="new-password"
					bind:value={next}
					required
					aria-invalid={errors.new_password ? true : undefined}
				/>
				<Field.Description>{m.password_rules()}</Field.Description>
				{#if errors.new_password}<Field.Error>{errors.new_password}</Field.Error>{/if}
			</Field.Field>
			<div class="flex items-center gap-2">
				<Checkbox id="pw-revoke" bind:checked={revoke} />
				<Label for="pw-revoke">{m.password_revoke_tokens()}</Label>
			</div>
			{#if problem}<p role="alert" class="text-sm text-destructive">{problem}</p>{/if}
			<div>
				<Button type="submit" disabled={busy || current === '' || next === ''}>
					{m.password_change()}
				</Button>
			</div>
		</form>
	</Card.Content>
</Card.Root>
