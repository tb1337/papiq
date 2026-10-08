<script lang="ts">
	import X from '@lucide/svelte/icons/x';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { reportError } from '#lib/errors.ts';
	import type { Drawer, UserName } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';

	type Level = components['schemas']['ShareLevel'];

	// Who may use a drawer: "read" shows its green documents, "read and write" also files into it.
	let {
		open = $bindable(false),
		drawer,
		users,
		onchanged
	}: {
		open?: boolean;
		drawer: Drawer;
		users: readonly UserName[];
		onchanged: () => void;
	} = $props();

	let userId = $state('');
	let level = $state<Level>('read');
	let busy = $state(false);

	const shares = $derived(drawer.shares ?? []);
	const name = (id: string) => users.find((user) => user.id === id)?.username ?? id;
	const candidates = $derived(
		users.filter(
			(user) => user.id !== session.user?.id && !shares.some((share) => share.user_id === user.id)
		)
	);
	const levels = $derived([
		{ value: 'read', label: m.share_read() },
		{ value: 'read_write', label: m.share_read_write() }
	]);

	async function set(id: string, next: Level) {
		busy = true;
		try {
			await unwrap(
				api.PUT('/api/v1/drawers/{id}/shares/{user_id}', {
					params: { path: { id: drawer.id, user_id: id } },
					body: { level: next }
				})
			);
			toast.success(m.share_saved());
			onchanged();
			userId = '';
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}

	async function revoke(id: string) {
		busy = true;
		try {
			await unwrap(
				api.DELETE('/api/v1/drawers/{id}/shares/{user_id}', {
					params: { path: { id: drawer.id, user_id: id } }
				})
			);
			toast.success(m.share_removed());
			onchanged();
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{m.share_title({ name: drawer.name })}</Dialog.Title>
			<Dialog.Description>{m.share_hint()}</Dialog.Description>
		</Dialog.Header>

		{#if shares.length === 0}
			<p class="text-sm text-muted-foreground">{m.share_none()}</p>
		{:else}
			<ul class="flex flex-col gap-2">
				{#each shares as share (share.user_id)}
					<li class="flex items-center gap-2">
						<span class="min-w-0 flex-1 truncate">{name(share.user_id)}</span>
						<NativeSelect
							aria-label={m.share_level_of({ name: name(share.user_id) })}
							value={share.level}
							options={levels}
							disabled={busy}
							onchange={(value) => set(share.user_id, value as Level)}
							class="h-10 w-44"
						/>
						<Button
							variant="ghost"
							size="icon"
							disabled={busy}
							aria-label={m.share_remove({ name: name(share.user_id) })}
							onclick={() => revoke(share.user_id)}
						>
							<X aria-hidden="true" />
						</Button>
					</li>
				{/each}
			</ul>
		{/if}

		{#if candidates.length > 0}
			<div class="flex flex-col gap-3 border-t pt-4">
				<Field.Field>
					<Field.Label for="share-user">{m.share_add()}</Field.Label>
					<NativeSelect
						id="share-user"
						bind:value={userId}
						options={[
							{ value: '', label: m.share_choose_user() },
							...candidates.map((user) => ({ value: user.id, label: user.username }))
						]}
					/>
				</Field.Field>
				<Field.Field>
					<Field.Label for="share-level">{m.share_level()}</Field.Label>
					<NativeSelect
						id="share-level"
						value={level}
						options={levels}
						onchange={(value) => (level = value as Level)}
					/>
				</Field.Field>
				<div>
					<Button disabled={busy || !userId} onclick={() => set(userId, level)}>
						{m.share_grant()}
					</Button>
				</div>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>
