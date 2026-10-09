<script lang="ts">
	import { untrack } from 'svelte';
	import { goto } from '$app/navigation';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import FieldValueInput from '#lib/components/FieldValueInput.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import RuleEffects from '#lib/components/rules/RuleEffects.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { sameValue, toApi, toInput, type ApiValue, type InputValue } from '#lib/fields.ts';
	import { BASE } from '#lib/base.ts';
	import { describeValue, fieldLabel } from '#lib/describe.ts';
	import { describeError, fieldErrors as fieldMessages } from '#lib/errors.ts';
	import { fieldsFor, type Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import X from '@lucide/svelte/icons/x';

	type Document = components['schemas']['DocumentDetails'];
	type Patch = components['schemas']['DocumentPatch'];
	type Preview = components['schemas']['DocumentPreviewOut'];

	// Edit the metadata. "Review changes" asks the API what the change would do (dry run); the
	// change is stored only after that is confirmed.
	let {
		document,
		lookup,
		onsaved
	}: { document: Document; lookup: Lookup; onsaved: (document: Document) => void } = $props();

	let title = $state('');
	let date = $state('');
	let contact = $state('');
	let documentType = $state('');
	let tags = $state<string[]>([]);
	let values = $state<Record<string, InputValue>>({});
	let busy = $state(false);
	let preview = $state<Preview | null>(null);
	let patch = $state<Patch>({});
	let fieldErrors = $state<Record<string, string>>({});
	let formError = $state<string | null>(null);

	const snapshot = () => JSON.stringify([title, date, contact, documentType, tags, values]);
	let baseline: { id: string; form: string } | null = null;

	// The form starts from the stored state, and again after a reload of the document, unless the
	// user has changed something meanwhile: an event must not wipe their input.
	function fromDocument() {
		title = document.title;
		date = document.document_date ?? '';
		contact = document.contact_id ?? '';
		documentType = document.document_type_id ?? '';
		tags = [...document.tag_ids];
		values = Object.fromEntries(
			lookup.fields.map((field) => [field.id, toInput(field.data_type, document.fields[field.id])])
		);
		baseline = { id: document.id, form: snapshot() };
	}

	$effect(() => {
		void document;
		void lookup.fields;
		untrack(() => {
			const edited = baseline?.id === document.id && snapshot() !== baseline.form;
			if (!edited) fromDocument();
		});
	});

	// Fields that fit the chosen type, and those the document already has.
	const shown = $derived(
		lookup.fields.filter(
			(field) =>
				fieldsFor([field], documentType || null).length > 0 ||
				document.fields[field.id] !== undefined
		)
	);
	const tagName = $derived(new Map(lookup.tags.map((tag) => [tag.id, tag.name])));
	const freeTags = $derived(lookup.tags.filter((tag) => !tags.includes(tag.id)));
	const options = (list: readonly { id: string; name: string }[]) => [
		{ value: '', label: m.value_unset() },
		...list.map((entry) => ({ value: entry.id, label: entry.name }))
	];

	/** What differs from the stored document, in the form of the API (null removes a value). */
	function buildPatch(): Patch {
		const next: Patch = {};
		if (title.trim() !== document.title) next.title = title.trim();
		if ((date || null) !== document.document_date) next.document_date = date || null;
		if ((contact || null) !== document.contact_id) next.contact_id = contact || null;
		if ((documentType || null) !== document.document_type_id) {
			next.document_type_id = documentType || null;
		}
		if (JSON.stringify([...tags].sort()) !== JSON.stringify([...document.tag_ids].sort())) {
			next.tag_ids = tags;
		}
		const changed: Record<string, ApiValue | null> = {};
		for (const field of shown) {
			const value = toApi(field.data_type, values[field.id]);
			if (!sameValue(value, document.fields[field.id])) changed[field.id] = value;
		}
		if (Object.keys(changed).length > 0) next.fields = changed;
		return next;
	}

	const dirty = $derived(Object.keys(buildPatch()).length > 0);

	function readProblem(error: unknown): void {
		fieldErrors = fieldMessages(error);
		formError = Object.keys(fieldErrors).length === 0 ? describeError(error) : null;
	}

	async function review(event: SubmitEvent) {
		event.preventDefault();
		patch = buildPatch();
		if (Object.keys(patch).length === 0 || busy) return;
		busy = true;
		try {
			preview = await unwrap(
				api.POST('/api/v1/documents/{id}/dry-run', {
					params: { path: { id: document.id } },
					body: patch
				})
			);
			fieldErrors = {};
			formError = null;
		} catch (error) {
			readProblem(error);
		} finally {
			busy = false;
		}
	}

	async function save() {
		busy = true;
		try {
			const result = await unwrap(
				api.PATCH('/api/v1/documents/{id}', {
					params: { path: { id: document.id } },
					body: patch
				})
			);
			preview = null;
			baseline = null;
			toast.success(m.document_saved());
			if ('title' in result) onsaved(result);
			else {
				// The rules filed the document where the caller can no longer read it.
				toast.info(m.document_moved_away());
				await goto(`${BASE}/documents`);
			}
		} catch (error) {
			preview = null;
			readProblem(error);
		} finally {
			busy = false;
		}
	}

	function reset() {
		fieldErrors = {};
		formError = null;
		fromDocument();
	}
</script>

<form class="flex flex-col gap-4" onsubmit={review} novalidate>
	<Field.Field>
		<Field.Label for="doc-title">{m.field_title()}</Field.Label>
		<Input id="doc-title" bind:value={title} aria-invalid={fieldErrors.title ? true : undefined} />
		{#if fieldErrors.title}<Field.Error>{fieldErrors.title}</Field.Error>{/if}
	</Field.Field>
	<Field.Field>
		<Field.Label for="doc-date">{m.field_date()}</Field.Label>
		<Input id="doc-date" type="date" bind:value={date} />
	</Field.Field>
	<Field.Field>
		<Field.Label for="doc-contact">{m.field_contact()}</Field.Label>
		<NativeSelect id="doc-contact" bind:value={contact} options={options(lookup.contacts)} />
	</Field.Field>
	<Field.Field>
		<Field.Label for="doc-type">{m.field_document_type()}</Field.Label>
		<NativeSelect id="doc-type" bind:value={documentType} options={options(lookup.documentTypes)} />
	</Field.Field>
	<Field.Field>
		<Field.Label for="doc-tag">{m.field_tags()}</Field.Label>
		<div class="flex flex-wrap gap-2">
			{#each tags as id (id)}
				<span
					class="inline-flex h-8 items-center gap-1 rounded-full bg-secondary pr-1 pl-3 text-sm"
				>
					{tagName.get(id) ?? id}
					<button
						type="button"
						class="grid size-6 place-items-center rounded-full hover:bg-background"
						aria-label={m.filter_tag_remove({ name: tagName.get(id) ?? id })}
						onclick={() => (tags = tags.filter((tag) => tag !== id))}
					>
						<X class="size-3.5" aria-hidden="true" />
					</button>
				</span>
			{/each}
		</div>
		{#key tags.length}
			<NativeSelect
				id="doc-tag"
				value=""
				options={options(freeTags).map((option, index) =>
					index === 0 ? { ...option, label: m.field_tag_add() } : option
				)}
				onchange={(value) => value && (tags = [...tags, value])}
			/>
		{/key}
	</Field.Field>

	{#each shown as field (field.id)}
		<FieldValueInput
			{field}
			bind:value={values[field.id]}
			id="field-def-{field.id}"
			error={fieldErrors[`field:${field.id}`]}
		/>
	{/each}

	{#if formError}<p class="text-sm text-destructive" role="alert">{formError}</p>{/if}

	<div class="flex gap-2">
		<Button type="submit" disabled={!dirty || busy}>{m.edit_review()}</Button>
		<Button type="button" variant="ghost" disabled={!dirty || busy} onclick={reset}>
			{m.edit_discard()}
		</Button>
	</div>
</form>

<Dialog.Root open={preview !== null} onOpenChange={(open) => !open && (preview = null)}>
	<Dialog.Content class="max-w-xl">
		<Dialog.Header>
			<Dialog.Title>{m.dryrun_title()}</Dialog.Title>
			<Dialog.Description>{m.dryrun_hint()}</Dialog.Description>
		</Dialog.Header>
		{#if preview}
			<div class="flex max-h-[60vh] flex-col gap-4 overflow-auto text-sm">
				<section>
					<h3 class="mb-1 font-medium">{m.dryrun_changes()}</h3>
					<ul class="flex flex-col gap-1">
						{#each preview.changed as field (field)}
							<li>
								<span class="text-muted-foreground">{fieldLabel(field, lookup)}</span>
							</li>
						{/each}
					</ul>
				</section>
				{#if preview.rules && preview.rules.length > 0}
					<section>
						<h3 class="mb-1 font-medium">{m.dryrun_rules()}</h3>
						<ul class="flex flex-col gap-2">
							{#each preview.rules as rule (rule.rule_id)}
								<li class="rounded-xl border p-3">
									<span class="font-medium">{rule.name}</span>
									<div class="mt-1">
										<RuleEffects effects={rule.applied} notes={rule.notes} {lookup} />
									</div>
								</li>
							{/each}
						</ul>
					</section>
				{/if}
				{#if preview.visibility}
					<section>
						<h3 class="mb-1 font-medium">{m.dryrun_visible()}</h3>
						<p>
							{m.dryrun_drawer({
								drawer: describeValue(preview.visibility.drawer_id, lookup)
							})}
						</p>
						<p class="text-muted-foreground">
							{[
								describeValue(preview.visibility.owner_id, lookup),
								preview.visibility.drawer_owner_id
									? describeValue(preview.visibility.drawer_owner_id, lookup)
									: null,
								...(preview.visibility.shares ?? []).map((share) =>
									describeValue(share.user_id, lookup)
								)
							]
								.filter((name, index, all) => name && all.indexOf(name) === index)
								.join(', ')}
						</p>
					</section>
				{/if}
			</div>
		{/if}
		<Dialog.Footer>
			<Button variant="ghost" disabled={busy} onclick={() => (preview = null)}>{m.cancel()}</Button>
			<Button disabled={busy} onclick={save}>{m.edit_save()}</Button>
		</Dialog.Footer>
	</Dialog.Content>
</Dialog.Root>
