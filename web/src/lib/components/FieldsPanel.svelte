<script lang="ts">
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { BASE } from '#lib/base.ts';
	import MasterRow from '#lib/components/MasterRow.svelte';
	import { describeError } from '#lib/errors.ts';
	import { fieldTypeLabel } from '#lib/fields.ts';
	import { fieldMatches } from '#lib/master-search.ts';
	import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import type { FieldDefinition, MasterData } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { phone } from '#lib/phone.svelte.ts';
	import { scope } from '#lib/scope.svelte.ts';

	// The field definitions, found by the search of the page: name, data type, set by hand only,
	// the types they apply to and whether they are required there, what belongs in them. The
	// key templates use stands in the editor (and here, where there is room).
	let { query = '' }: { query?: string } = $props();

	let fields = $state<FieldDefinition[] | null>(null);
	let types = $state<MasterData[]>([]);
	let problem = $state<string | null>(null);
	const listed = $derived(
		(fields ?? []).filter((field) => query.trim() === '' || fieldMatches(field, query))
	);

	async function load() {
		try {
			[fields, types] = await Promise.all([
				unwrap(
					api.GET('/api/v1/fields', {
						params: { query: { document_counts: true, all_users: scope.query } }
					})
				),
				unwrap(api.GET('/api/v1/document-types'))
			]);
			problem = null;
		} catch (failure) {
			problem = describeError(failure);
		}
	}
	// Again after a dialog saved (wherever it was opened), and with another scope.
	$effect(() => {
		void masterDataDialogs.changed;
		void scope.allUsers;
		void load();
	});

	/** "All types · required", "Invoice · optional", "Invoice (required), Payslip". */
	function scopeText(field: FieldDefinition): string {
		const need = (required: boolean) =>
			required ? m.field_def_required_short() : m.field_def_optional_short();
		if (field.document_type_ids === null) {
			return `${m.field_def_all_types()} · ${need(field.required)}`;
		}
		const name = (id: string) => types.find((type) => type.id === id)?.name ?? id;
		const required = (id: string) => field.required_document_type_ids.includes(id);
		if (field.document_type_ids.length === 0) return m.field_def_no_type();
		if (field.document_type_ids.length === 1) {
			const [only] = field.document_type_ids;
			return `${name(only)} · ${need(required(only))}`;
		}
		return field.document_type_ids
			.map((id) => (required(id) ? m.field_def_required_in({ scope: name(id) }) : name(id)))
			.join(', ');
	}
</script>

{#if problem}
	<p class="text-destructive" role="alert">{problem}</p>
{:else if !fields}
	<p class="text-muted-foreground">{m.loading()}</p>
{:else if listed.length === 0}
	<p class="rounded-2xl border border-dashed bg-card px-6 py-10 text-center text-muted-foreground">
		{fields.length === 0 ? m.master_none() : m.assignment_none_found()}
	</p>
{:else}
	<ul class="divide-y overflow-hidden rounded-2xl border bg-card" aria-label={m.master_fields()}>
		{#each listed as field (field.id)}
			<MasterRow
				onopen={() => masterDataDialogs.open('fields', field)}
				editLabel={m.master_edit({ name: field.name })}
				usage={[{ count: field.document_count, href: `${BASE}/documents?field=${field.id}` }]}
			>
				<span class="flex max-w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
					<span class="break-words">{field.name}</span>
					<span class="rounded-md bg-secondary px-1.5 text-xs leading-5 text-muted-foreground">
						{fieldTypeLabel(field.data_type)}
					</span>
					{#if field.assignment === 'manual'}
						<span
							class="rounded-md bg-(--tag-orange) px-1.5 text-xs leading-5 text-(--tag-orange-foreground)"
						>
							{m.field_def_manual_short()}
						</span>
					{/if}
					{#if !phone.current}
						<code class="font-mono text-xs text-muted-foreground" title={m.field_def_key_hint()}
							>{`field.${field.key}`}</code
						>
					{/if}
				</span>
				<span class="text-sm text-muted-foreground">{scopeText(field)}</span>
				{#if field.description || field.choices.length > 0}
					<span class="line-clamp-1 text-sm text-muted-foreground">
						{field.description || field.choices.join(', ')}
					</span>
				{/if}
			</MasterRow>
		{/each}
	</ul>
{/if}
