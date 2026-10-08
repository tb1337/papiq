<script lang="ts">
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import NameDialog from '#lib/components/NameDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import type { MasterData } from '#lib/masterdata.svelte.ts';
	import { KINDS, type SimpleKind } from '#lib/masterdata-api.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Contacts, document types or tags: a list with add, rename and delete.
	let { kind }: { kind: SimpleKind } = $props();
	const ops = $derived(KINDS[kind]);

	let items = $state<MasterData[] | null>(null);
	let problem = $state<string | null>(null);
	let creating = $state(false);
	let renaming = $state<MasterData | null>(null);
	let removing = $state<MasterData | null>(null);

	async function load() {
		try {
			items = await ops.list();
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	onMount(() => void load());

	async function create(name: string) {
		await ops.create(name);
		toast.success(m.master_created());
		await load();
	}
	async function rename(name: string) {
		if (!renaming) return;
		await ops.rename(renaming.id, name);
		toast.success(m.master_renamed());
		await load();
	}
	async function remove() {
		if (!removing) return;
		await ops.remove(removing.id);
		toast.success(m.master_deleted());
		await load();
	}
</script>

<div class="flex flex-col gap-4">
	<div>
		<Button onclick={() => (creating = true)}>
			<Plus aria-hidden="true" />
			{ops.add()}
		</Button>
	</div>
	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !items}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if items.length === 0}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-10 text-center text-muted-foreground"
		>
			{m.master_none()}
		</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each items as item (item.id)}
				<li class="flex items-center gap-3 rounded-2xl border bg-card px-4 py-2">
					<span class="min-w-0 flex-1 truncate">{item.name}</span>
					<Button
						variant="ghost"
						size="icon"
						aria-label={m.master_rename({ name: item.name })}
						onclick={() => (renaming = item)}
					>
						<Pencil aria-hidden="true" />
					</Button>
					<Button
						variant="ghost"
						size="icon"
						aria-label={m.master_delete({ name: item.name })}
						onclick={() => (removing = item)}
					>
						<Trash2 aria-hidden="true" />
					</Button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<NameDialog bind:open={creating} title={ops.add()} submitLabel={m.create()} onsubmit={create} />
<NameDialog
	bind:open={() => renaming !== null, (open) => !open && (renaming = null)}
	title={m.master_rename({ name: renaming?.name ?? '' })}
	initial={renaming?.name ?? ''}
	onsubmit={rename}
/>
<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.master_delete({ name: removing?.name ?? '' })}
	description={m.master_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
