<script lang="ts" module>
	import FileText from '@lucide/svelte/icons/file-text';
	import Shapes from '@lucide/svelte/icons/shapes';
	import Users from '@lucide/svelte/icons/users';
	import { formatNumber } from '#lib/i18n.ts';
	import { m } from '#lib/paraglide/messages.js';

	export type Unit = 'documents' | 'contacts' | 'types';

	const ICONS = { documents: FileText, contacts: Users, types: Shapes };

	/** "3 documents", "1 contact", "12 types". */
	export function usageLabel(count: number, unit: Unit = 'documents'): string {
		const many = { count: formatNumber(count) };
		if (unit === 'contacts')
			return count === 1 ? m.master_contacts_one() : m.master_contacts_many(many);
		if (unit === 'types') return count === 1 ? m.master_types_one() : m.master_types_many(many);
		return count === 1 ? m.master_documents_one() : m.master_documents(many);
	}
</script>

<script lang="ts">
	// How often an item of master data is used (in the scope setting): its documents, a group's
	// or topic's contacts, a type group's types. A link to where they are listed; none, plain.
	let {
		count,
		href,
		unit = 'documents'
	}: { count: number | null | undefined; href: string; unit?: Unit } = $props();
	const label = $derived(usageLabel(count ?? 0, unit));
	const Icon = $derived(ICONS[unit]);
</script>

{#if count === null || count === undefined}
	<span class="w-24" aria-hidden="true"></span>
{:else if count === 0}
	<span class="shrink-0 px-2 text-sm text-muted-foreground tabular-nums">{label}</span>
{:else}
	<a
		{href}
		class="inline-flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-1 text-sm text-brand tabular-nums hover:bg-panel hover:underline"
	>
		<Icon class="size-3.5" aria-hidden="true" />
		{label}
	</a>
{/if}
