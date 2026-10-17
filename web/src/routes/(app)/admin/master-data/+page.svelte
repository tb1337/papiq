<script lang="ts">
	import Plus from '@lucide/svelte/icons/plus';
	import SearchIcon from '@lucide/svelte/icons/search';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import FieldsPanel from '#lib/components/FieldsPanel.svelte';
	import MasterDataList from '#lib/components/MasterDataList.svelte';
	import TagsPanel from '#lib/components/TagsPanel.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Tabs from '#lib/components/ui/tabs/index.ts';
	import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { phone } from '#lib/phone.svelte.ts';
	import { session } from '#lib/session.svelte.ts';
	import { kindFilter } from '#lib/tag-kinds.ts';

	const TABS = ['contacts', 'document-types', 'tags', 'fields'] as const;
	type Tab = (typeof TABS)[number];

	// Four tabs: contacts, document types, every kind of tag, fields. The tab stands in the URL
	// (an unknown one is the first); the API refuses everyone but admins. Each tab has its own
	// search (in this browser) and adds with the button beside it, on a phone with the round one
	// beside the title.
	const tab = $derived<Tab>(
		(TABS as readonly string[]).includes(page.url.searchParams.get('tab') ?? '')
			? (page.url.searchParams.get('tab') as Tab)
			: 'contacts'
	);
	const queries = $state<Record<Tab, string>>({
		contacts: '',
		'document-types': '',
		tags: '',
		fields: ''
	});
	const ADD: Record<Tab, () => string> = {
		contacts: m.master_add_contact,
		'document-types': m.master_add_type,
		tags: m.master_add_tag,
		fields: m.master_add_field
	};
	const SEARCH: Record<Tab, () => string> = {
		contacts: m.master_search_contacts,
		'document-types': m.master_search_types,
		tags: m.master_search_tags,
		fields: m.master_search_fields
	};
	const LABEL: Record<Tab, () => string> = {
		contacts: m.master_contacts,
		'document-types': m.master_types,
		tags: m.master_tags,
		fields: m.master_fields
	};

	/** A new item of the tab; on the Tags tab of the kind it shows, else the kind comes first. */
	function add() {
		if (tab === 'tags') masterDataDialogs.open(kindFilter(page.url.searchParams) ?? 'any-tag');
		else masterDataDialogs.open(tab);
	}
</script>

<svelte:head><title>{m.nav_master_data()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-4 sm:gap-6">
	<div class="flex items-center justify-between gap-3">
		<h1 class="text-2xl font-semibold tracking-tight sm:text-[1.75rem]">{m.nav_master_data()}</h1>
		{#if session.isAdmin && phone.current}
			<Button size="icon" class="size-11 shrink-0" aria-label={ADD[tab]()} onclick={add}>
				<Plus class="size-5" aria-hidden="true" />
			</Button>
		{/if}
	</div>

	{#if !session.isAdmin}
		<p class="text-muted-foreground">{m.admin_only()}</p>
	{:else}
		<Tabs.Root value={tab} onValueChange={(value) => goto(`?tab=${value}`, { reset: false })}>
			<!-- Four equal tabs on a phone, a shorter name for the types. -->
			<Tabs.List
				class="grid h-auto w-full grid-cols-4 group-data-[orientation=horizontal]/tabs:h-auto sm:inline-flex sm:w-fit"
			>
				{#each TABS as value (value)}
					<Tabs.Trigger {value} class="min-h-11 px-1 sm:min-h-8 sm:px-3">
						{value === 'document-types' && phone.current ? m.master_types_short() : LABEL[value]()}
					</Tabs.Trigger>
				{/each}
			</Tabs.List>
			<div class="mt-4 flex flex-col gap-3 sm:gap-4">
				<div class="flex items-center gap-3">
					<label
						class="flex h-11 min-w-0 flex-1 items-center gap-2 rounded-xl border border-input bg-card px-3.5 text-muted-foreground focus-within:border-ring sm:h-10 sm:rounded-full dark:bg-input/30"
					>
						<SearchIcon class="size-4 shrink-0" aria-hidden="true" />
						<input
							type="search"
							bind:value={queries[tab]}
							placeholder={SEARCH[tab]()}
							aria-label={m.master_search_label({ what: LABEL[tab]() })}
							class="h-full min-w-0 flex-1 bg-transparent text-base text-foreground outline-none placeholder:text-muted-foreground sm:text-sm"
						/>
					</label>
					{#if !phone.current}
						<Button onclick={add}>
							<Plus aria-hidden="true" />
							{ADD[tab]()}
						</Button>
					{/if}
				</div>
				{#key tab}
					{#if tab === 'fields'}
						<FieldsPanel query={queries.fields} />
					{:else if tab === 'tags'}
						<TagsPanel query={queries.tags} />
					{:else}
						<MasterDataList kind={tab} query={queries[tab]} />
					{/if}
				{/key}
			</div>
		</Tabs.Root>
	{/if}
</div>
