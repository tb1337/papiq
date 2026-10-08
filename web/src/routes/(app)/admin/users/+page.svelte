<script lang="ts">
	import Plus from '@lucide/svelte/icons/plus';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import UserManage from '#lib/components/UserManage.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import * as Table from '#lib/components/ui/table/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';

	type User = components['schemas']['UserOut'];
	type Role = components['schemas']['Role'];

	let users = $state<User[] | null>(null);
	let problem = $state<string | null>(null);
	let managing = $state<string | null>(null);
	let creating = $state(false);
	let username = $state('');
	let role = $state<Role>('user');
	let password = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function load() {
		try {
			users = await unwrap(api.GET('/api/v1/users'));
			problem = null;
		} catch (failure) {
			problem = describeError(failure);
		}
	}
	onMount(() => {
		if (session.isAdmin) void load();
	});

	function openCreate() {
		username = '';
		role = 'user';
		password = '';
		error = null;
		creating = true;
	}

	async function create(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		error = null;
		try {
			await unwrap(
				api.POST('/api/v1/users', {
					body: { username: username.trim(), role, ...(password ? { password } : {}) }
				})
			);
			toast.success(m.user_created());
			creating = false;
			await load();
		} catch (failure) {
			error = describeError(failure);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{m.nav_users()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_users()}</h1>
		{#if session.isAdmin}
			<Button onclick={openCreate}>
				<Plus aria-hidden="true" />
				{m.user_new()}
			</Button>
		{/if}
	</div>

	{#if !session.isAdmin}
		<p class="text-muted-foreground">{m.admin_only()}</p>
	{:else if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{problem}</p>
			<Button variant="outline" onclick={load}>{m.retry()}</Button>
		</div>
	{:else if !users}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<Table.Root>
			<Table.Header>
				<Table.Row>
					<Table.Head>{m.login_username()}</Table.Head>
					<Table.Head>{m.user_role()}</Table.Head>
					<Table.Head>{m.user_state()}</Table.Head>
					<Table.Head>{m.user_created_at()}</Table.Head>
					<Table.Head><span class="sr-only">{m.user_manage()}</span></Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each users as user (user.id)}
					<Table.Row>
						<Table.Cell class="font-medium">{user.username}</Table.Cell>
						<Table.Cell>{user.role === 'admin' ? m.role_admin() : m.role_user()}</Table.Cell>
						<Table.Cell>
							<Badge variant={user.active ? 'secondary' : 'outline'}>
								{user.active ? m.user_active() : m.user_inactive()}
							</Badge>
						</Table.Cell>
						<Table.Cell>{user.created_at ? formatDate(user.created_at) : '–'}</Table.Cell>
						<Table.Cell class="text-right">
							<Button variant="outline" size="sm" onclick={() => (managing = user.id)}>
								{m.user_manage()}
							</Button>
						</Table.Cell>
					</Table.Row>
				{/each}
			</Table.Body>
		</Table.Root>
	{/if}
</div>

<Dialog.Root bind:open={creating}>
	<Dialog.Content>
		<form class="flex flex-col gap-4" onsubmit={create} novalidate>
			<Dialog.Header><Dialog.Title>{m.user_new()}</Dialog.Title></Dialog.Header>
			<Field.Field>
				<Field.Label for="new-username">{m.login_username()}</Field.Label>
				<Input
					id="new-username"
					bind:value={username}
					maxlength={150}
					autocomplete="off"
					required
				/>
			</Field.Field>
			<Field.Field>
				<Field.Label for="new-role">{m.user_role()}</Field.Label>
				<NativeSelect
					id="new-role"
					value={role}
					options={[
						{ value: 'user', label: m.role_user() },
						{ value: 'admin', label: m.role_admin() }
					]}
					onchange={(value) => (role = value as Role)}
				/>
			</Field.Field>
			<Field.Field>
				<Field.Label for="new-password">{m.user_start_password()}</Field.Label>
				<Input
					id="new-password"
					type="password"
					autocomplete="new-password"
					bind:value={password}
				/>
				<Field.Description>{m.password_rules()}</Field.Description>
			</Field.Field>
			{#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (creating = false)}
					>{m.cancel()}</Button
				>
				<Button type="submit" disabled={busy || username.trim() === ''}>{m.create()}</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

{#if managing}
	<UserManage
		bind:open={() => managing !== null, (open) => !open && (managing = null)}
		userId={managing}
		onchanged={load}
	/>
{/if}
