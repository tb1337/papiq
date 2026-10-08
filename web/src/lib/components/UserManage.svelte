<script lang="ts">
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';

	type Account = components['schemas']['AccountOut'];
	type Role = components['schemas']['Role'];

	// Everything an admin does with one account. Own account: no password reset, no removal of the
	// second factor (the API refuses both; there are the settings for that).
	let {
		open = $bindable(false),
		userId,
		onchanged
	}: { open?: boolean; userId: string; onchanged: () => void } = $props();

	let account = $state<Account | null>(null);
	let problem = $state<string | null>(null);
	let busy = $state(false);
	let password = $state('');
	let revoke = $state(false);
	let resetError = $state<string | null>(null);
	let confirm = $state<'totp' | 'oidc' | 'delete' | null>(null);

	const own = $derived(account?.id === session.user?.id);

	async function load() {
		account = null;
		problem = null;
		try {
			const data = await unwrap(
				api.GET('/api/v1/users/{id}', { params: { path: { id: userId } } })
			);
			if ('has_password' in data) account = data;
			else problem = m.error_forbidden();
		} catch (error) {
			problem = describeError(error);
		}
	}

	$effect(() => {
		if (open) {
			password = '';
			revoke = false;
			resetError = null;
			void load();
		}
	});

	async function run(action: () => Promise<unknown>, done: string) {
		busy = true;
		try {
			await action();
			toast.success(done);
			onchanged();
			await load();
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}

	const setRole = (role: string) =>
		run(
			() =>
				unwrap(
					api.PATCH('/api/v1/users/{id}', {
						params: { path: { id: userId } },
						body: { role: role as Role }
					})
				),
			m.user_saved()
		);

	const setActive = (active: boolean) =>
		run(
			() =>
				unwrap(
					api.PATCH('/api/v1/users/{id}', { params: { path: { id: userId } }, body: { active } })
				),
			m.user_saved()
		);

	async function resetPassword(event: SubmitEvent) {
		event.preventDefault();
		busy = true;
		resetError = null;
		try {
			await unwrap(
				api.POST('/api/v1/users/{id}/password', {
					params: { path: { id: userId } },
					body: { password, revoke_tokens: revoke }
				})
			);
			toast.success(m.user_password_reset_done());
			password = '';
		} catch (error) {
			resetError = describeError(error);
		} finally {
			busy = false;
		}
	}

	async function confirmed() {
		if (confirm === 'totp') {
			await unwrap(api.DELETE('/api/v1/users/{id}/totp', { params: { path: { id: userId } } }));
			toast.success(m.user_totp_removed());
		} else if (confirm === 'oidc') {
			await unwrap(api.DELETE('/api/v1/users/{id}/oidc', { params: { path: { id: userId } } }));
			toast.success(m.user_oidc_removed());
		} else if (confirm === 'delete') {
			await unwrap(api.DELETE('/api/v1/users/{id}', { params: { path: { id: userId } } }));
			toast.success(m.user_deleted());
			open = false;
		}
		onchanged();
		if (confirm !== 'delete') await load();
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-lg">
		<Dialog.Header>
			<Dialog.Title>{account?.username ?? m.loading()}</Dialog.Title>
		</Dialog.Header>
		{#if problem}
			<p class="text-destructive" role="alert">{problem}</p>
		{:else if account}
			<div class="flex max-h-[70vh] flex-col gap-5 overflow-auto">
				<Field.Field>
					<Field.Label for="user-role">{m.user_role()}</Field.Label>
					<NativeSelect
						id="user-role"
						value={account.role ?? 'user'}
						disabled={busy}
						options={[
							{ value: 'user', label: m.role_user() },
							{ value: 'admin', label: m.role_admin() }
						]}
						onchange={setRole}
					/>
				</Field.Field>

				<div class="flex items-center justify-between gap-3">
					<span>{account.active ? m.user_active() : m.user_inactive()}</span>
					<Button variant="outline" disabled={busy} onclick={() => setActive(!account!.active)}>
						{account.active ? m.user_deactivate() : m.user_activate()}
					</Button>
				</div>

				{#if !own}
					<form class="flex flex-col gap-3 border-t pt-4" onsubmit={resetPassword} novalidate>
						<Field.Field>
							<Field.Label for="user-password">{m.user_password_reset()}</Field.Label>
							<Input
								id="user-password"
								type="password"
								autocomplete="new-password"
								bind:value={password}
								aria-invalid={resetError ? true : undefined}
							/>
							{#if resetError}<Field.Error>{resetError}</Field.Error>{:else}
								<Field.Description>{m.password_rules()}</Field.Description>
							{/if}
						</Field.Field>
						<label class="flex items-center gap-2 text-sm">
							<input type="checkbox" bind:checked={revoke} class="size-4 accent-primary" />
							{m.user_revoke_tokens()}
						</label>
						<div>
							<Button type="submit" disabled={busy || password === ''}
								>{m.user_password_reset()}</Button
							>
						</div>
					</form>
				{/if}

				<div class="flex flex-col gap-2 border-t pt-4">
					<p class="text-sm text-muted-foreground">
						{account.totp_enabled ? m.user_totp_on() : m.user_totp_off()}
						·
						{account.linked_accounts.length > 0 ? m.user_oidc_on() : m.user_oidc_off()}
					</p>
					<div class="flex flex-wrap gap-2">
						{#if account.totp_enabled && !own}
							<Button variant="outline" disabled={busy} onclick={() => (confirm = 'totp')}>
								{m.user_totp_remove()}
							</Button>
						{/if}
						{#if account.linked_accounts.length > 0}
							<Button variant="outline" disabled={busy} onclick={() => (confirm = 'oidc')}>
								{m.user_oidc_remove()}
							</Button>
						{/if}
						{#if !own}
							<Button variant="destructive" disabled={busy} onclick={() => (confirm = 'delete')}>
								{m.user_delete()}
							</Button>
						{/if}
					</div>
				</div>
			</div>
		{/if}
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={() => confirm !== null, (value) => !value && (confirm = null)}
	title={confirm === 'totp'
		? m.user_totp_remove()
		: confirm === 'oidc'
			? m.user_oidc_remove()
			: m.user_delete()}
	description={confirm === 'totp'
		? m.user_totp_remove_confirm()
		: confirm === 'oidc'
			? m.user_oidc_remove_confirm()
			: m.user_delete_confirm()}
	confirmLabel={m.confirm_yes()}
	onconfirm={confirmed}
/>
