<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import DocumentThumb from '#lib/components/DocumentThumb.svelte';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import NamesLine from '#lib/components/NamesLine.svelte';
	import ProcessingProgress from '#lib/components/ProcessingProgress.svelte';
	import TagChips from '#lib/components/TagChips.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { formatDate } from '#lib/i18n.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { session } from '#lib/session.svelte.ts';

	// One document as a card of the grid: its first page, the lane on it, title, contact (with
	// its logo or initials) and type (with its icon) each with its shown groups, date, its shown
	// tags, for admins the owner of another user's document; `children` adds a line.
	let {
		document,
		lookup,
		href = `${BASE}/documents/${document.id}`,
		children
	}: {
		document: components['schemas']['DocumentDetails'];
		lookup: Lookup | null;
		href?: string;
		children?: Snippet;
	} = $props();

	const contact = $derived(lookup?.contacts.find((entry) => entry.id === document.contact_id));
	const kind = $derived(
		lookup?.documentTypes.find((entry) => entry.id === document.document_type_id)
	);
	const owner = $derived(
		session.isAdmin && document.owner_id !== session.user?.id
			? (lookup?.users.find((user) => user.id === document.owner_id)?.username ?? null)
			: null
	);
</script>

<a
	{href}
	class="group flex h-full min-w-0 flex-col overflow-hidden rounded-2xl border bg-card transition-colors hover:border-primary"
>
	<div class="relative aspect-[3/4] overflow-hidden border-b bg-secondary">
		<DocumentThumb id={document.id} class="size-full" />
		<span class="absolute top-2 right-2"><LaneBadge lane={document.lane} /></span>
	</div>
	<div class="flex min-w-0 flex-1 flex-col gap-1 p-3">
		<span class="line-clamp-2 leading-snug font-medium group-hover:underline">
			{document.title}
		</span>
		<NamesLine
			parts={[{ name: contact?.name, groupIds: contact?.group_ids, avatar: contact }]}
			tags={lookup?.tags ?? []}
			class="text-sm text-muted-foreground"
		/>
		<NamesLine
			parts={[{ name: kind?.name, groupIds: kind?.group_ids, icon: kind?.icon }]}
			date={formatDate(document.document_date ?? document.created_at)}
			tags={lookup?.tags ?? []}
			class="text-xs text-muted-foreground"
		/>
		<TagChips {document} {lookup} />
		{#if owner}<Badge variant="outline" class="w-fit">{owner}</Badge>{/if}
		{#if document.processing.status === 'processing'}
			<ProcessingProgress processing={document.processing} compact class="mt-1 max-w-48" />
		{/if}
		{@render children?.()}
	</div>
</a>
