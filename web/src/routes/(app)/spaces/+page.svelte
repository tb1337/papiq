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
	import SpaceShares from '#lib/components/SpaceShares.svelte';
	import NameDialog from '#lib/components/NameDialog.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import type { Space, UserName } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { canManageSpace } from '#lib/permissions.ts';
	import { session } from '#lib/session.svelte.ts';

	let spaces = $state<Space[] | null>(null);
	let users = $state<UserName[]>([]);
	let problem = $state<string | null>(null);
	let creating = $state(false);
	let renaming = $state<Space | null>(null);
	let removing = $state<Space | null>(null);
	let sharing = $state<string | null>(null);

	const me = $derived(session.user?.id);
	const own = $derived((spaces ?? []).filter((space) => space.owner_id === me));
	// Admins list every space; theirs to manage, apart from the ones shared with them.
	const sharedWithMe = (space: Space) =>
		!session.isAdmin || (space.shares ?? []).some((share) => share.user_id === me);
	const shared = $derived(
		(spaces ?? []).filter((space) => space.owner_id !== me && sharedWithMe(space))
	);
	const others = $derived(
		(spaces ?? []).filter((space) => space.owner_id !== me && !sharedWithMe(space))
	);
	const sharedSpace = $derived(spaces?.find((space) => space.id === sharing) ?? null);
	const ownerName = (id: string) => users.find((user) => user.id === id)?.username ?? id;

	async function load() {
		try {
			[spaces, users] = await Promise.all([
				unwrap(api.GET('/api/v1/spaces')),
				unwrap(api.GET('/api/v1/users'))
			]);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	onMount(() => void load());

	async function create(name: string) {
		await unwrap(api.POST('/api/v1/spaces', { body: { name } }));
		toast.success(m.space_created());
		await load();
	}

	async function rename(name: string) {
		if (!renaming) return;
		await unwrap(
			api.PATCH('/api/v1/spaces/{id}', {
				params: { path: { id: renaming.id } },
				body: { name }
			})
		);
		toast.success(m.space_renamed());
		await load();
	}

	async function remove() {
		if (!removing) return;
		await unwrap(api.DELETE('/api/v1/spaces/{id}', { params: { path: { id: removing.id } } }));
		toast.success(m.space_deleted());
		await load();
	}
</script>

<svelte:head><title>{m.nav_spaces()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_spaces()}</h1>
		<Button onclick={() => (creating = true)}>
			<Plus aria-hidden="true" />
			{m.space_new()}
		</Button>
	</div>

	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{problem}</p>
			<Button variant="outline" onclick={load}>{m.retry()}</Button>
		</div>
	{:else if !spaces}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else}
		<section class="flex flex-col gap-3" aria-labelledby="own-spaces">
			<h2 id="own-spaces" class="text-lg font-semibold">{m.spaces_own()}</h2>
			<ul class="flex flex-col gap-2">
				{#each own as space (space.id)}
					{@render managed(space)}
				{/each}
			</ul>
		</section>

		{#if shared.length > 0}
			<section class="flex flex-col gap-3" aria-labelledby="shared-spaces">
				<h2 id="shared-spaces" class="text-lg font-semibold">{m.spaces_shared()}</h2>
				<ul class="flex flex-col gap-2">
					{#each shared as space (space.id)}
						{#if canManageSpace(space, session.user)}
							{@render managed(space)}
						{:else}
							<li class="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4">
								<Archive class="size-5 text-muted-foreground" aria-hidden="true" />
								<span class="min-w-0 flex-1 truncate font-medium">{space.name}</span>
								<span class="text-sm text-muted-foreground">
									{m.space_from({ owner: ownerName(space.owner_id) })} ·
									{space.access === 'read_write' ? m.share_read_write() : m.share_read()}
								</span>
							</li>
						{/if}
					{/each}
				</ul>
			</section>
		{/if}

		{#if others.length > 0}
			<section class="flex flex-col gap-3" aria-labelledby="other-spaces">
				<h2 id="other-spaces" class="text-lg font-semibold">{m.spaces_others()}</h2>
				<ul class="flex flex-col gap-2">
					{#each others as space (space.id)}
						{@render managed(space)}
					{/each}
				</ul>
			</section>
		{/if}
	{/if}
</div>

{#snippet managed(space: Space)}
	<li class="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4">
		<Archive class="size-5 text-muted-foreground" aria-hidden="true" />
		<div class="flex min-w-0 flex-1 flex-col gap-1">
			<span class="flex flex-wrap items-center gap-2 font-medium">
				<span class="truncate">{space.name}</span>
				{#if space.is_default}<Badge variant="secondary">{m.space_default()}</Badge>{/if}
				{#if space.owner_id !== me}
					<Badge variant="outline">{ownerName(space.owner_id)}</Badge>
				{/if}
			</span>
			{#if (space.shares ?? []).length > 0}
				<span class="text-sm text-muted-foreground">
					{m.space_shared_with({
						names: (space.shares ?? []).map((share) => ownerName(share.user_id)).join(', ')
					})}
				</span>
			{/if}
		</div>
		<div class="flex gap-1">
			{#if !space.is_default}
				<Button
					variant="ghost"
					size="icon"
					aria-label={m.space_share({ name: space.name })}
					onclick={() => (sharing = space.id)}
				>
					<Share2 aria-hidden="true" />
				</Button>
			{/if}
			<Button
				variant="ghost"
				size="icon"
				aria-label={m.space_rename({ name: space.name })}
				onclick={() => (renaming = space)}
			>
				<Pencil aria-hidden="true" />
			</Button>
			{#if !space.is_default}
				<Button
					variant="ghost"
					size="icon"
					aria-label={m.space_delete({ name: space.name })}
					onclick={() => (removing = space)}
				>
					<Trash2 aria-hidden="true" />
				</Button>
			{/if}
		</div>
	</li>
{/snippet}

<NameDialog bind:open={creating} title={m.space_new()} submitLabel={m.create()} onsubmit={create} />
<NameDialog
	bind:open={() => renaming !== null, (open) => !open && (renaming = null)}
	title={m.space_rename({ name: renaming?.name ?? '' })}
	initial={renaming?.name ?? ''}
	onsubmit={rename}
/>
<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.space_delete({ name: removing?.name ?? '' })}
	description={m.space_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
{#if sharedSpace}
	<SpaceShares
		bind:open={() => sharing !== null, (open) => !open && (sharing = null)}
		space={sharedSpace}
		{users}
		onchanged={load}
	/>
{/if}
