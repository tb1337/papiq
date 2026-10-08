<script lang="ts">
	import CircleAlert from '@lucide/svelte/icons/circle-alert';
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Clock from '@lucide/svelte/icons/clock';
	import Upload from '@lucide/svelte/icons/upload';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { writableDrawers } from '#lib/permissions.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';
	import { uploads } from '#lib/upload.svelte.ts';
	import { drawerLabel, type Drawer, type UserName } from '#lib/masterdata.svelte.ts';
	import { cn } from '#lib/utils.ts';

	let {
		open = $bindable(false),
		drawers,
		users
	}: { open?: boolean; drawers: readonly Drawer[]; users: readonly UserName[] } = $props();

	// Uploads go into the caller's own drawers or those shared for writing, any for admins;
	// empty = default.
	const choices = $derived([
		{ value: '', label: m.upload_default_drawer() },
		...writableDrawers(drawers, session.user).map((drawer) => ({
			value: drawer.id,
			label: drawerLabel(drawer, users, session.user?.id)
		}))
	]);
	let drawerId = $state('');
	let over = $state(false);
	let input = $state<HTMLInputElement | null>(null);

	function take(list: FileList | null) {
		if (list && list.length > 0) uploads.add([...list], drawerId || null);
		if (input) input.value = '';
	}

	function drop(event: DragEvent) {
		event.preventDefault();
		over = false;
		take(event.dataTransfer?.files ?? null);
	}
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="max-w-xl">
		<Dialog.Header>
			<Dialog.Title>{m.upload_title()}</Dialog.Title>
			<Dialog.Description>{m.upload_hint()}</Dialog.Description>
		</Dialog.Header>

		<Field.Field>
			<Field.Label for="upload-drawer">{m.upload_drawer()}</Field.Label>
			<NativeSelect id="upload-drawer" bind:value={drawerId} options={choices} />
		</Field.Field>

		<div
			role="presentation"
			ondragover={(event) => {
				event.preventDefault();
				over = true;
			}}
			ondragleave={() => (over = false)}
			ondrop={drop}
			class={cn(
				'flex flex-col items-center gap-3 rounded-2xl border border-dashed px-6 py-10 text-center text-muted-foreground transition-colors',
				over && 'border-primary bg-secondary'
			)}
		>
			<Upload class="size-7" aria-hidden="true" />
			<p>{m.upload_drop()}</p>
			<Button variant="outline" onclick={() => input?.click()}>{m.upload_choose()}</Button>
			<input
				bind:this={input}
				type="file"
				multiple
				accept="application/pdf,image/jpeg,image/png,image/tiff"
				class="sr-only"
				tabindex="-1"
				aria-label={m.upload_choose()}
				onchange={() => take(input?.files ?? null)}
			/>
		</div>

		{#if uploads.items.length > 0}
			<ul class="flex max-h-64 flex-col gap-2 overflow-auto" aria-live="polite">
				{#each uploads.items as item (item.key)}
					<li class="flex items-center gap-3 rounded-xl border bg-card px-3 py-2 text-sm">
						<span class="min-w-0 flex-1 truncate" title={item.name}>{item.name}</span>
						{#if item.state === 'done'}
							<LaneBadge lane={item.lane} />
						{:else if item.state === 'failed'}
							<span class="flex items-center gap-1.5 text-destructive" role="alert">
								<CircleAlert class="size-4 shrink-0" aria-hidden="true" />
								{item.message}
							</span>
						{:else}
							<span class="flex items-center gap-1.5 text-muted-foreground">
								<Clock class="size-4 shrink-0" aria-hidden="true" />
								{#if item.state === 'waiting'}{m.upload_waiting()}
								{:else if item.state === 'uploading'}{m.upload_uploading()}
								{:else}{m.upload_processing()}{/if}
							</span>
						{/if}
					</li>
				{/each}
			</ul>
			<Dialog.Footer>
				<Button variant="ghost" disabled={uploads.active} onclick={() => uploads.clearFinished()}>
					<CircleCheck aria-hidden="true" />
					{m.upload_clear()}
				</Button>
			</Dialog.Footer>
		{/if}
	</Dialog.Content>
</Dialog.Root>
