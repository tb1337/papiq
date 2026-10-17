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

	// One document in a list: preview, title, contact (with its logo or initials) and type
	// (with its icon) each with its shown groups, date, its shown tags, lane, for admins the
	// owner of another user's document; `children` adds a line.
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
	class="flex items-center gap-4 rounded-2xl border bg-card p-3 transition-colors hover:border-primary"
>
	<DocumentThumb id={document.id} class="h-20 w-14 shrink-0 rounded-lg border" />
	<div class="flex min-w-0 flex-1 flex-col gap-1">
		<span class="flex min-w-0 items-center gap-2">
			<span class="truncate font-medium">{document.title}</span>
			{#if owner}<Badge variant="outline" class="shrink-0">{owner}</Badge>{/if}
		</span>
		<NamesLine
			parts={[
				{ name: contact?.name, groupIds: contact?.group_ids, avatar: contact },
				{ name: kind?.name, groupIds: kind?.group_ids, icon: kind?.icon }
			]}
			date={formatDate(document.document_date ?? document.created_at)}
			tags={lookup?.tags ?? []}
			class="text-sm text-muted-foreground"
		/>
		<TagChips {document} {lookup} />
		{#if document.processing.status === 'processing'}
			<ProcessingProgress processing={document.processing} compact class="mt-1 max-w-48" />
		{/if}
		{@render children?.()}
	</div>
	<LaneBadge lane={document.lane} />
</a>
