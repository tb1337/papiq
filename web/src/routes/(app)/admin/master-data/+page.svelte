<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import AttributesPanel from '#lib/components/AttributesPanel.svelte';
	import MasterDataList from '#lib/components/MasterDataList.svelte';
	import * as Tabs from '#lib/components/ui/tabs/index.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';

	const TABS = ['contacts', 'document-types', 'tags', 'attributes'] as const;
	type Tab = (typeof TABS)[number];

	// The tab stands in the URL; the API refuses everyone but admins.
	const tab = $derived<Tab>(
		(TABS as readonly string[]).includes(page.url.searchParams.get('tab') ?? '')
			? (page.url.searchParams.get('tab') as Tab)
			: 'contacts'
	);
</script>

<svelte:head><title>{m.nav_master_data()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_master_data()}</h1>

	{#if !session.isAdmin}
		<p class="text-muted-foreground">{m.admin_only()}</p>
	{:else}
		<Tabs.Root value={tab} onValueChange={(value) => goto(`?tab=${value}`, { reset: false })}>
			<Tabs.List>
				<Tabs.Trigger value="contacts">{m.master_contacts()}</Tabs.Trigger>
				<Tabs.Trigger value="document-types">{m.master_types()}</Tabs.Trigger>
				<Tabs.Trigger value="tags">{m.master_tags()}</Tabs.Trigger>
				<Tabs.Trigger value="attributes">{m.master_attributes()}</Tabs.Trigger>
			</Tabs.List>
			<div class="mt-4">
				{#key tab}
					{#if tab === 'attributes'}
						<AttributesPanel />
					{:else}
						<MasterDataList kind={tab} />
					{/if}
				{/key}
			</div>
		</Tabs.Root>
	{/if}
</div>
