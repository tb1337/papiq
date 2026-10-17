<script lang="ts">
	import type { Snippet } from 'svelte';
	import Globe from '@lucide/svelte/icons/globe';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import Upload from '@lucide/svelte/icons/upload';
	import { toast } from 'svelte-sonner';
	import ContactAvatar from '#lib/components/ContactAvatar.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { domainsOf, fetchLogo, removeLogo, uploadLogo } from '#lib/logos.ts';
	import type { Contact } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';

	// A contact's logo in its dialog: what it shows (the logo or its initials) and where the logo
	// came from; upload (PNG, JPEG or WebP), remove, and, where the server fetches logos and the
	// contact has a domain, fetch it from the website (never in place of an uploaded one). Each
	// action is saved at once, apart from the dialog's own fields. With `beside` (a phone), the
	// logo stands beside it (the name field), the actions and where the logo came from below, as
	// in the editor's draft (without the hint on file types).
	let {
		contact,
		fetching,
		onchanged,
		beside
	}: {
		contact: Pick<Contact, 'id' | 'name' | 'logo' | 'logo_failure' | 'identifiers'>;
		/** The server fetches logos. */
		fetching: boolean;
		onchanged: () => void;
		/** What stands beside the logo, the actions below both. */
		beside?: Snippet;
	} = $props();

	let logo = $state<Contact['logo']>(null);
	let failure = $state<Contact['logo_failure']>(null);
	let busy = $state(false);
	let input = $state<HTMLInputElement | null>(null);

	$effect(() => {
		logo = contact.logo;
		failure = contact.logo_failure;
	});

	const canFetch = $derived(
		fetching && logo?.source !== 'uploaded' && domainsOf(contact).length > 0
	);

	async function run(action: () => Promise<void>) {
		if (busy) return;
		busy = true;
		try {
			await action();
		} catch (problem) {
			toast.error(describeError(problem));
		} finally {
			busy = false;
		}
	}

	function chosen(event: Event & { currentTarget: HTMLInputElement }) {
		const file = event.currentTarget.files?.[0];
		event.currentTarget.value = '';
		if (!file) return;
		void run(async () => {
			const saved = await uploadLogo(contact.id, file);
			logo = saved.logo;
			failure = saved.logo_failure;
			toast.success(m.logo_saved());
			onchanged();
		});
	}
</script>

{#snippet origin()}
	{#if logo?.source === 'uploaded'}
		<span>{m.logo_uploaded({ date: formatDate(logo.stored_at) })}</span>
	{:else if logo?.source === 'fetched'}
		<span class="break-words">
			{m.logo_fetched({ domain: logo.domain ?? '', date: formatDate(logo.stored_at) })}
		</span>
	{:else}
		<span class="text-muted-foreground">{m.logo_none()}</span>
	{/if}
	{#if failure}
		<span class="text-xs break-words text-muted-foreground">
			{m.logo_failed({
				domains: failure.domains.join(', '),
				date: formatDate(failure.failed_at)
			})}
		</span>
	{/if}
{/snippet}

{#snippet actions()}
	<!-- Beside the name (a phone): small pills without icons, as in the draft, each taking a tap
	     of 44 pixels (32 and 6 above and below). -->
	{@const pill = beside
		? 'relative h-8 rounded-full px-3 text-[0.8125rem] after:absolute after:inset-x-0 after:-inset-y-1.5'
		: ''}
	<div class="flex flex-wrap gap-2">
		<input
			bind:this={input}
			type="file"
			accept="image/png,image/jpeg,image/webp"
			class="hidden"
			onchange={chosen}
			tabindex="-1"
			aria-hidden="true"
		/>
		<Button variant="outline" size="sm" class={pill} disabled={busy} onclick={() => input?.click()}>
			{#if !beside}<Upload aria-hidden="true" />{/if}
			{m.logo_upload()}
		</Button>
		{#if canFetch}
			<!-- Short beside the name; its name still says what it fetches. -->
			<Button
				variant={beside ? 'outline' : 'ghost'}
				size="sm"
				class={pill}
				aria-label={beside ? m.logo_fetch() : undefined}
				disabled={busy}
				onclick={() =>
					run(async () => {
						await fetchLogo(contact.id);
						toast.info(m.logo_fetch_queued());
					})}
			>
				{#if !beside}<Globe aria-hidden="true" />{/if}
				{beside ? m.logo_fetch_short() : m.logo_fetch()}
			</Button>
		{/if}
		{#if logo}
			<Button
				variant="ghost"
				size="sm"
				class={pill}
				disabled={busy}
				onclick={() =>
					run(async () => {
						await removeLogo(contact.id);
						logo = null;
						failure = null;
						toast.success(m.logo_removed());
						onchanged();
					})}
			>
				{#if !beside}<Trash2 aria-hidden="true" />{/if}
				{m.logo_remove()}
			</Button>
		{/if}
	</div>
{/snippet}

{#snippet hint()}
	<p class="text-xs text-muted-foreground">
		{m.logo_upload_hint()}
		{#if canFetch}{m.logo_fetch_hint()}{/if}
	</p>
{/snippet}

{#if beside}
	<!-- The logo beside the name (56 pixels and a gap of 12), what to do with it below both. -->
	<div class="flex flex-col gap-2" data-logo-beside>
		<div class="flex items-center gap-3">
			<ContactAvatar contact={{ name: contact.name, logo }} size="xl" />
			<div class="min-w-0 flex-1">{@render beside()}</div>
		</div>
		<div class="flex flex-col gap-2 pl-17">
			{@render actions()}
			<div class="flex flex-col text-xs">{@render origin()}</div>
		</div>
	</div>
{:else}
	<div class="flex flex-col gap-2">
		<div class="flex items-center gap-3">
			<ContactAvatar contact={{ name: contact.name, logo }} size="lg" />
			<div class="flex min-w-0 flex-col text-sm">{@render origin()}</div>
		</div>
		{@render actions()}
		{@render hint()}
	</div>
{/if}
