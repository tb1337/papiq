<script lang="ts">
	import Archive from '@lucide/svelte/icons/archive';
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Share2 from '@lucide/svelte/icons/share-2';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import DrawerShares from '#lib/components/DrawerShares.svelte';
	import NameDialog from '#lib/components/NameDialog.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import type { Drawer, UserName } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { canManageDrawer } from '#lib/permissions.ts';
	import { session } from '#lib/session.svelte.ts';

	let drawers = $state<Drawer[] | null>(null);
	let users = $state<UserName[]>([]);
	let problem = $state<string | null>(null);
	let creating = $state(false);
	let renaming = $state<Drawer | null>(null);
	let removing = $state<Drawer | null>(null);
	let sharing = $state<string | null>(null);

	const me = $derived(session.user?.id);
	const own = $derived((drawers ?? []).filter((drawer) => drawer.owner_id === me));
	// Admins list every drawer; theirs to manage, apart from the ones shared with them.
	const sharedWithMe = (drawer: Drawer) =>
		!session.isAdmin || (drawer.shares ?? []).some((share) => share.user_id === me);
	const shared = $derived(
		(drawers ?? []).filter((drawer) => drawer.owner_id !== me && sharedWithMe(drawer))
	);
	const others = $derived(
		(drawers ?? []).filter((drawer) => drawer.owner_id !== me && !sharedWithMe(drawer))
	);
	const sharedDrawer = $derived(drawers?.find((drawer) => drawer.id === sharing) ?? null);
	const ownerName = (id: string) => users.find((user) => user.id === id)?.username ?? id;

	async function load() {
		try {
			[drawers, users] = await Promise.all([
				unwrap(api.GET('/api/v1/drawers')),
				unwrap(api.GET('/api/v1/users'))
			]);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	onMount(() => void load());

	async function create(name: string) {
		await unwrap(api.POST('/api/v1/drawers', { body: { name } }));
		toast.success(m.drawer_created());
		await load();
	}

	async function rename(name: string) {
		if (!renaming) return;
		await unwrap(
			api.PATCH('/api/v1/drawers/{id}', {
				params: { path: { id: renaming.id } },
				body: { name }
			})
		);
		toast.success(m.drawer_renamed());
		await load();
	}

	async function remove() {
		if (!removing) return;
		await unwrap(api.DELETE('/api/v1/drawers/{id}', { params: { path: { id: removing.id } } }));
		toast.success(m.drawer_deleted());
		await load();
	}
</script>

<svelte:head><title>{m.nav_drawers()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_drawers()}</h1>
		<Button onclick={() => (creating = true)}>
			<Plus aria-hidden="true" />
			{m.drawer_new()}
		</Button>
	</div>

	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{problem}</p>
			<Button variant="outline" onclick={load}>{m.retry()}</Button>
		</div>
	{:else if !drawers}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<section class="flex flex-col gap-3" aria-labelledby="own-drawers">
			<h2 id="own-drawers" class="text-lg font-semibold">{m.drawers_own()}</h2>
			<ul class="flex flex-col gap-2">
				{#each own as drawer (drawer.id)}
					{@render managed(drawer)}
				{/each}
			</ul>
		</section>

		{#if shared.length > 0}
			<section class="flex flex-col gap-3" aria-labelledby="shared-drawers">
				<h2 id="shared-drawers" class="text-lg font-semibold">{m.drawers_shared()}</h2>
				<ul class="flex flex-col gap-2">
					{#each shared as drawer (drawer.id)}
						{#if canManageDrawer(drawer, session.user)}
							{@render managed(drawer)}
						{:else}
							<li class="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4">
								<Archive class="size-5 text-muted-foreground" aria-hidden="true" />
								<span class="min-w-0 flex-1 truncate font-medium">{drawer.name}</span>
								<span class="text-sm text-muted-foreground">
									{m.drawer_from({ owner: ownerName(drawer.owner_id) })} ·
									{drawer.access === 'read_write' ? m.share_read_write() : m.share_read()}
								</span>
							</li>
						{/if}
					{/each}
				</ul>
			</section>
		{/if}

		{#if others.length > 0}
			<section class="flex flex-col gap-3" aria-labelledby="other-drawers">
				<h2 id="other-drawers" class="text-lg font-semibold">{m.drawers_others()}</h2>
				<ul class="flex flex-col gap-2">
					{#each others as drawer (drawer.id)}
						{@render managed(drawer)}
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>

{#snippet managed(drawer: Drawer)}
	<li class="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4">
		<Archive class="size-5 text-muted-foreground" aria-hidden="true" />
		<div class="flex min-w-0 flex-1 flex-col gap-1">
			<span class="flex flex-wrap items-center gap-2 font-medium">
				<span class="truncate">{drawer.name}</span>
				{#if drawer.is_default}<Badge variant="secondary">{m.drawer_default()}</Badge>{/if}
				{#if drawer.owner_id !== me}
					<Badge variant="outline">{ownerName(drawer.owner_id)}</Badge>
				{/if}
			</span>
			{#if (drawer.shares ?? []).length > 0}
				<span class="text-sm text-muted-foreground">
					{m.drawer_shared_with({
						names: (drawer.shares ?? []).map((share) => ownerName(share.user_id)).join(', ')
					})}
				</span>
			{/if}
		</div>
		<div class="flex gap-1">
			{#if !drawer.is_default}
				<Button
					variant="ghost"
					size="icon"
					aria-label={m.drawer_share({ name: drawer.name })}
					onclick={() => (sharing = drawer.id)}
				>
					<Share2 aria-hidden="true" />
				</Button>
			{/if}
			<Button
				variant="ghost"
				size="icon"
				aria-label={m.drawer_rename({ name: drawer.name })}
				onclick={() => (renaming = drawer)}
			>
				<Pencil aria-hidden="true" />
			</Button>
			{#if !drawer.is_default}
				<Button
					variant="ghost"
					size="icon"
					aria-label={m.drawer_delete({ name: drawer.name })}
					onclick={() => (removing = drawer)}
				>
					<Trash2 aria-hidden="true" />
				</Button>
			{/if}
		</div>
	</li>
{/snippet}

<NameDialog
	bind:open={creating}
	title={m.drawer_new()}
	submitLabel={m.create()}
	onsubmit={create}
/>
<NameDialog
	bind:open={() => renaming !== null, (open) => !open && (renaming = null)}
	title={m.drawer_rename({ name: renaming?.name ?? '' })}
	initial={renaming?.name ?? ''}
	onsubmit={rename}
/>
<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.drawer_delete({ name: removing?.name ?? '' })}
	description={m.drawer_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
{#if sharedDrawer}
	<DrawerShares
		bind:open={() => sharing !== null, (open) => !open && (sharing = null)}
		drawer={sharedDrawer}
		{users}
		onchanged={load}
	/>
{/if}
