<script lang="ts">
	import History from '@lucide/svelte/icons/history';
	import KeyRound from '@lucide/svelte/icons/key-round';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Send from '@lucide/svelte/icons/send';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import SecretDialog from '#lib/components/SecretDialog.svelte';
	import WebhookDialog from '#lib/components/WebhookDialog.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button, buttonVariants } from '#lib/components/ui/button/index.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import type { UserName } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	type Webhook = components['schemas']['WebhookOut'];

	let hooks = $state<Webhook[] | null>(null);
	let users = $state<UserName[]>([]);
	let problem = $state<string | null>(null);
	let editing = $state<Webhook | 'new' | null>(null);
	let removing = $state<Webhook | null>(null);
	let renewing = $state<Webhook | null>(null);
	let secret = $state<{ webhook: string; value: string } | null>(null);
	let busy = $state(false);

	const ownerName = (id: string) => users.find((user) => user.id === id)?.username ?? id;

	async function load() {
		try {
			[hooks, users] = await Promise.all([
				unwrap(api.GET('/api/v1/webhooks')),
				unwrap(api.GET('/api/v1/users'))
			]);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}
	onMount(() => void load());

	async function remove() {
		if (!removing) return;
		await unwrap(api.DELETE('/api/v1/webhooks/{id}', { params: { path: { id: removing.id } } }));
		toast.success(m.webhook_deleted());
		await load();
	}

	async function renew() {
		if (!renewing) return;
		const renewed = await unwrap(
			api.POST('/api/v1/webhooks/{id}/secret', { params: { path: { id: renewing.id } } })
		);
		secret = { webhook: renewed.name, value: renewed.secret };
		await load();
	}

	async function test(hook: Webhook) {
		busy = true;
		try {
			const delivery = await unwrap(
				api.POST('/api/v1/webhooks/{id}/test', { params: { path: { id: hook.id } } })
			);
			if (delivery.outcome === 'delivered')
				toast.success(m.webhook_test_ok({ status: delivery.status_code ?? 0 }));
			else
				toast.error(
					m.webhook_test_failed({ reason: delivery.error ?? String(delivery.status_code ?? '') })
				);
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}
</script>

<svelte:head><title>{m.nav_webhooks()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_webhooks()}</h1>
		<Button onclick={() => (editing = 'new')}>
			<Plus aria-hidden="true" />
			{m.webhook_new()}
		</Button>
	</div>

	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{problem}</p>
			<Button variant="outline" onclick={load}>{m.retry()}</Button>
		</div>
	{:else if !hooks}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if hooks.length === 0}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{m.webhook_none()}
		</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each hooks as hook (hook.id)}
				<li class="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4">
					<div class="flex min-w-0 flex-1 flex-col gap-1">
						<span class="flex flex-wrap items-center gap-2 font-medium">
							<span class="truncate">{hook.name}</span>
							<Badge variant={hook.active ? 'secondary' : 'outline'}>
								{hook.active ? m.webhook_on() : m.webhook_off()}
							</Badge>
							{#if session.isAdmin && hook.owner_id !== session.user?.id}
								<Badge variant="outline">{ownerName(hook.owner_id)}</Badge>
							{/if}
						</span>
						<span class="truncate font-mono text-sm text-muted-foreground">{hook.url}</span>
						{#if hook.disabled_reason === 'failing'}
							<span class="text-sm text-destructive"
								>{m.webhook_failing({ count: hook.failed_streak })}</span
							>
						{/if}
					</div>
					<div class="flex flex-wrap gap-1">
						{#if hook.owner_id === session.user?.id}
							<Button
								variant="ghost"
								size="icon"
								disabled={busy}
								aria-label={m.webhook_test({ name: hook.name })}
								onclick={() => test(hook)}
							>
								<Send aria-hidden="true" />
							</Button>
						{/if}
						<a
							href="{BASE}/webhooks/{hook.id}"
							aria-label={m.webhook_log({ name: hook.name })}
							class={cn(buttonVariants({ variant: 'ghost', size: 'icon' }))}
						>
							<History aria-hidden="true" />
						</a>
						{#if hook.owner_id === session.user?.id}
							<Button
								variant="ghost"
								size="icon"
								aria-label={m.webhook_renew({ name: hook.name })}
								onclick={() => (renewing = hook)}
							>
								<KeyRound aria-hidden="true" />
							</Button>
						{/if}
						<Button
							variant="ghost"
							size="icon"
							aria-label={m.webhook_edit_named({ name: hook.name })}
							onclick={() => (editing = hook)}
						>
							<Pencil aria-hidden="true" />
						</Button>
						<Button
							variant="ghost"
							size="icon"
							aria-label={m.webhook_delete({ name: hook.name })}
							onclick={() => (removing = hook)}
						>
							<Trash2 aria-hidden="true" />
						</Button>
					</div>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<WebhookDialog
	bind:open={() => editing !== null, (open) => !open && (editing = null)}
	webhook={editing === 'new' ? null : editing}
	onsaved={load}
	oncreated={(created) => (secret = { webhook: created.name, value: created.secret })}
/>
<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.webhook_delete({ name: removing?.name ?? '' })}
	description={m.webhook_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
<ConfirmDialog
	bind:open={() => renewing !== null, (open) => !open && (renewing = null)}
	title={m.webhook_renew({ name: renewing?.name ?? '' })}
	description={m.webhook_renew_confirm()}
	confirmLabel={m.webhook_renew_do()}
	destructive={false}
	onconfirm={renew}
/>
<SecretDialog
	secret={secret?.value ?? null}
	title={m.webhook_secret_title({ name: secret?.webhook ?? '' })}
	description={m.webhook_secret_hint()}
	onclose={() => (secret = null)}
/>
