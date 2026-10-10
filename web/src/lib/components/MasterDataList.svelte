<script lang="ts">
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import MasterDataDialog from '#lib/components/MasterDataDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { type Item, KINDS, type SimpleKind, type Values } from '#lib/masterdata-api.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Contacts, document types or tags: a list with add, change and delete.
	let { kind }: { kind: SimpleKind } = $props();
	const ops = $derived(KINDS[kind]);

	let items = $state<Item[] | null>(null);
	let problem = $state<string | null>(null);
	let creating = $state(false);
	let editing = $state<Item | null>(null);
	let removing = $state<Item | null>(null);

	async function load() {
		try {
			items = await ops.list();
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	onMount(() => void load());

	async function create(values: Values) {
		await ops.create(values);
		toast.success(m.master_created());
		await load();
	}
	async function change(values: Values) {
		if (!editing) return;
		await ops.change(editing.id, values);
		toast.success(m.master_saved());
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
					<div class="flex min-w-0 flex-1 flex-col">
						<span class="truncate">{item.name}</span>
						{#if item.aliases?.length}
							<span class="truncate text-sm text-muted-foreground">
								{m.master_aliases_list({ aliases: item.aliases.join(', ') })}
							</span>
						{:else if item.description}
							<span class="truncate text-sm text-muted-foreground">{item.description}</span>
						{/if}
					</div>
					<Button
						variant="ghost"
						size="icon"
						aria-label={m.master_edit({ name: item.name })}
						onclick={() => (editing = item)}
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

<MasterDataDialog
	bind:open={creating}
	title={ops.add()}
	extra={ops.extra}
	submitLabel={m.create()}
	onsubmit={create}
/>
<MasterDataDialog
	bind:open={() => editing !== null, (open) => !open && (editing = null)}
	title={m.master_edit({ name: editing?.name ?? '' })}
	extra={ops.extra}
	initial={editing}
	onsubmit={change}
/>
<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.master_delete({ name: removing?.name ?? '' })}
	description={m.master_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
