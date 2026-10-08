<script lang="ts">
	import type { Snippet } from 'svelte';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import DocumentThumb from '#lib/components/DocumentThumb.svelte';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import { formatDate } from '#lib/i18n.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';

	// One document in a list: preview, title, contact, type, date, lane; `children` adds a line.
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

	const line = $derived(
		[
			lookup?.contacts.find((entry) => entry.id === document.contact_id)?.name,
			lookup?.documentTypes.find((entry) => entry.id === document.document_type_id)?.name,
			formatDate(document.document_date ?? document.created_at)
		]
			.filter(Boolean)
			.join(' · ')
	);
</script>

<a
	{href}
	class="flex items-center gap-4 rounded-2xl border bg-card p-3 transition-colors hover:border-primary"
>
	<DocumentThumb id={document.id} class="h-20 w-14 shrink-0 rounded-lg border" />
	<div class="flex min-w-0 flex-1 flex-col gap-1">
		<span class="truncate font-medium">{document.title}</span>
		<span class="truncate text-sm text-muted-foreground">{line}</span>
		{@render children?.()}
	</div>
	<LaneBadge lane={document.lane} />
</a>
