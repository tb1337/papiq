<script lang="ts">
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import PasswordCard from '#lib/components/PasswordCard.svelte';
	import TokensCard from '#lib/components/TokensCard.svelte';
	import TotpCard from '#lib/components/TotpCard.svelte';
	import * as Alert from '#lib/components/ui/alert/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import { oidcErrorMessage } from '#lib/oidc-errors.ts';
	import { m } from '#lib/paraglide/messages.js';

	type Me = components['schemas']['Me'];
	type Oidc = components['schemas']['OidcInfo'];

	let me = $state<Me | null>(null);
	let oidc = $state<Oidc | null>(null);
	let problem = $state<string | null>(null);
	let unlinking = $state(false);
	const linkError = $derived(oidcErrorMessage(new URLSearchParams(page.url.search).get('error')));

	async function load() {
		try {
			[me, oidc] = await Promise.all([
				unwrap(api.GET('/api/v1/auth/me')),
				unwrap(api.GET('/api/v1/auth/oidc'))
			]);
		} catch (error) {
			problem = describeError(error);
		}
	}

	onMount(load);

	async function link() {
		try {
			const { authorization_url } = await unwrap(api.POST('/api/v1/auth/oidc/link'));
			window.location.assign(authorization_url);
		} catch (error) {
			reportError(error);
		}
	}

	async function unlink() {
		await unwrap(api.DELETE('/api/v1/auth/oidc/link'));
		toast.success(m.oidc_unlinked());
		await load();
	}

	async function endOthers() {
		const { ended } = await unwrap(api.DELETE('/api/v1/auth/sessions'));
		toast.success(m.sessions_ended({ count: ended }));
	}
	let ending = $state(false);
</script>

<svelte:head><title>{m.nav_settings()} · p:api:q</title></svelte:head>

<div class="mx-auto flex w-full max-w-3xl flex-col gap-6">
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_settings()}</h1>

	{#if linkError}
		<Alert.Root variant="destructive"><Alert.Description>{linkError}</Alert.Description></Alert.Root
		>
	{/if}

	{#if problem}
		<div class="flex flex-col items-start gap-3">
			<p class="text-destructive">{problem}</p>
			<Button variant="outline" onclick={load}>{m.retry()}</Button>
		</div>
	{:else if me === null}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<Card.Root>
			<Card.Header>
				<Card.Title>{me.user.username}</Card.Title>
				<Card.Description>
					{me.user.role === 'admin' ? m.role_admin() : m.role_user()}
				</Card.Description>
			</Card.Header>
		</Card.Root>

		{#if me.has_password}<PasswordCard />{/if}
		<TotpCard enabled={me.totp_enabled} onchanged={load} />

		{#if oidc?.enabled || me.linked_accounts.length > 0}
			<Card.Root>
				<Card.Header>
					<Card.Title>{oidc?.display_name ?? m.oidc_title()}</Card.Title>
					<Card.Description>
						{me.linked_accounts.length > 0 ? m.oidc_linked() : m.oidc_not_linked()}
					</Card.Description>
				</Card.Header>
				<Card.Content class="flex flex-wrap gap-2">
					{#if oidc?.enabled}
						<Button variant="outline" onclick={link}>{m.oidc_link()}</Button>
					{/if}
					{#if me.linked_accounts.length > 0}
						{#if me.has_password}
							<Button variant="outline" onclick={() => (unlinking = true)}>
								{m.oidc_unlink()}
							</Button>
						{:else}
							<p class="text-sm text-muted-foreground">{m.oidc_unlink_needs_password()}</p>
						{/if}
					{/if}
				</Card.Content>
			</Card.Root>
		{/if}

		<Card.Root>
			<Card.Header>
				<Card.Title>{m.sessions_title()}</Card.Title>
				<Card.Description>{m.sessions_hint()}</Card.Description>
			</Card.Header>
			<Card.Content>
				<Button variant="outline" onclick={() => (ending = true)}>{m.sessions_end_others()}</Button>
			</Card.Content>
		</Card.Root>

		<TokensCard />
	{/if}
</div>

<ConfirmDialog
	bind:open={unlinking}
	title={m.oidc_unlink()}
	description={m.oidc_unlink_confirm()}
	confirmLabel={m.oidc_unlink()}
	onconfirm={unlink}
/>
<ConfirmDialog
	bind:open={ending}
	title={m.sessions_end_others()}
	description={m.sessions_end_confirm()}
	confirmLabel={m.sessions_end_others()}
	destructive={false}
	onconfirm={endOthers}
/>
