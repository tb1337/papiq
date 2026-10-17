<script lang="ts">
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import type { Snippet } from 'svelte';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { phone } from '#lib/phone.svelte.ts';
	import { cn } from '#lib/utils.ts';

	// The frame of a master data editor: a form in a dialog whose body scrolls while its title
	// and its footer (delete, cancel, save) stay; on a phone a sheet over the whole screen with a
	// fixed bar on top ("Cancel" · title · "Save") and deleting at the end of the body. Deleting
	// asks first (`ondelete`, only for an item that exists).
	let {
		open = $bindable(false),
		title,
		shortTitle = title,
		description,
		submitLabel,
		busy = false,
		disabled = false,
		error = null,
		onsubmit,
		ondelete,
		deleteTitle = '',
		deleteDescription = '',
		class: className,
		children
	}: {
		open?: boolean;
		title: string;
		/** The title in the phone's bar: what is edited ("Contact"), not its name. */
		shortTitle?: string;
		description?: string;
		submitLabel: string;
		busy?: boolean;
		/** Saving is not possible yet (a name is missing). */
		disabled?: boolean;
		error?: string | null;
		onsubmit: (event: SubmitEvent) => void;
		ondelete?: () => Promise<void>;
		deleteTitle?: string;
		deleteDescription?: string;
		/** The width of the dialog. */
		class?: string;
		children: Snippet;
	} = $props();

	let confirming = $state(false);
</script>

<Dialog.Root bind:open>
	<Dialog.Content
		showCloseButton={!phone.current}
		class={cn(
			'flex flex-col gap-0 overflow-hidden p-0',
			phone.current
				? 'top-0 left-0 h-dvh max-h-dvh w-full max-w-none translate-x-0 translate-y-0 rounded-none bg-background ring-0'
				: cn('max-h-[calc(100dvh-2rem)] sm:max-w-xl', className)
		)}
	>
		<form class="flex min-h-0 flex-1 flex-col" {onsubmit} novalidate>
			{#if phone.current}
				<div
					class="grid h-14 shrink-0 grid-cols-[1fr_auto_1fr] items-center gap-1 border-b bg-card px-1"
				>
					<Button
						type="button"
						variant="ghost"
						class="h-11 justify-self-start px-3 text-base font-normal text-brand"
						onclick={() => (open = false)}
					>
						{m.cancel()}
					</Button>
					<Dialog.Title class="max-w-[50vw] truncate text-base font-semibold"
						>{shortTitle}</Dialog.Title
					>
					<Button
						type="submit"
						variant="ghost"
						class="h-11 justify-self-end px-3 text-base font-semibold text-brand"
						disabled={busy || disabled}
					>
						{m.editor_save_short()}
					</Button>
				</div>
			{:else}
				<Dialog.Header class="shrink-0 px-4 pt-4 pr-12 pb-3">
					<Dialog.Title>{title}</Dialog.Title>
					{#if description}<Dialog.Description>{description}</Dialog.Description>{/if}
				</Dialog.Header>
			{/if}
			{#if error}
				<p
					class="shrink-0 border-b bg-destructive/5 px-4 py-2 text-sm text-destructive"
					role="alert"
				>
					{error}
				</p>
			{/if}
			<div
				class={cn(
					'flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto overscroll-contain px-4 pb-4',
					phone.current && 'pt-4'
				)}
			>
				{#if phone.current && description}
					<p class="text-sm text-muted-foreground">{description}</p>
				{/if}
				{@render children()}
				{#if phone.current && ondelete}
					<Button
						type="button"
						variant="outline"
						class="mt-2 h-11 text-destructive"
						onclick={() => (confirming = true)}
					>
						<Trash2 aria-hidden="true" />
						{deleteTitle}
					</Button>
				{/if}
			</div>
			{#if !phone.current}
				<div class="flex shrink-0 flex-wrap items-center gap-2 border-t bg-muted/50 p-4">
					{#if ondelete}
						<Button
							type="button"
							variant="ghost"
							class="text-destructive hover:text-destructive"
							aria-label={deleteTitle}
							onclick={() => (confirming = true)}
						>
							<Trash2 aria-hidden="true" />
							{m.delete_title()}
						</Button>
					{/if}
					<div class="ml-auto flex gap-2">
						<Button type="button" variant="ghost" onclick={() => (open = false)}>
							{m.cancel()}
						</Button>
						<Button type="submit" disabled={busy || disabled}>{submitLabel}</Button>
					</div>
				</div>
			{/if}
		</form>
	</Dialog.Content>
</Dialog.Root>

{#if ondelete}
	<ConfirmDialog
		bind:open={confirming}
		title={deleteTitle}
		description={deleteDescription}
		confirmLabel={m.delete_title()}
		onconfirm={ondelete}
	/>
{/if}
