<script lang="ts">
	import Pencil from '@lucide/svelte/icons/pencil';
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Textarea } from '#lib/components/ui/textarea/index.ts';
	import type { components } from '#lib/api/schema.ts';
	import { describeError } from '#lib/errors.ts';
	import type { FieldDefinition, MasterData } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';

	type DataType = components['schemas']['FieldType'];
	const TYPES: DataType[] = ['text', 'number', 'amount', 'date', 'boolean', 'choice', 'link'];
	const typeLabel: Record<DataType, () => string> = {
		text: m.field_def_type_text,
		number: m.field_def_type_number,
		amount: m.field_def_type_amount,
		date: m.field_def_type_date,
		boolean: m.field_def_type_boolean,
		choice: m.field_def_type_choice,
		link: m.field_def_type_link
	};

	let fields = $state<FieldDefinition[] | null>(null);
	let types = $state<MasterData[]>([]);
	let problem = $state<string | null>(null);
	let editing = $state<FieldDefinition | 'new' | null>(null);
	let removing = $state<FieldDefinition | null>(null);

	// The form of the dialog.
	let name = $state('');
	let dataType = $state<DataType>('text');
	let global = $state(true);
	let scope = $state<string[]>([]);
	let choices = $state('');
	let error = $state<string | null>(null);
	let busy = $state(false);

	async function load() {
		try {
			[fields, types] = await Promise.all([
				unwrap(api.GET('/api/v1/fields')),
				unwrap(api.GET('/api/v1/document-types'))
			]);
			problem = null;
		} catch (failure) {
			problem = describeError(failure);
		}
	}
	onMount(() => void load());

	function open(target: FieldDefinition | 'new') {
		editing = target;
		error = null;
		const have = target === 'new' ? null : target;
		name = have?.name ?? '';
		dataType = have?.data_type ?? 'text';
		global = have ? have.document_type_ids === null : true;
		scope = have?.document_type_ids ? [...have.document_type_ids] : [];
		choices = have ? have.choices.join('\n') : '';
	}

	const scopeText = (field: FieldDefinition) =>
		field.document_type_ids === null
			? m.field_def_scope_global()
			: field.document_type_ids
					.map((id) => types.find((type) => type.id === id)?.name ?? id)
					.join(', ');

	async function save(event: SubmitEvent) {
		event.preventDefault();
		if (busy || editing === null) return;
		busy = true;
		error = null;
		const list = choices
			.split('\n')
			.map((line) => line.trim())
			.filter(Boolean);
		const document_type_ids = global ? null : scope;
		try {
			if (editing === 'new') {
				await unwrap(
					api.POST('/api/v1/fields', {
						body: {
							name: name.trim(),
							data_type: dataType,
							document_type_ids,
							...(dataType === 'choice' ? { choices: list } : {})
						}
					})
				);
			} else {
				await unwrap(
					api.PATCH('/api/v1/fields/{id}', {
						params: { path: { id: editing.id } },
						body: {
							name: name.trim(),
							document_type_ids,
							...(editing.data_type === 'choice' ? { choices: list } : {})
						}
					})
				);
			}
			toast.success(m.master_saved());
			editing = null;
			await load();
		} catch (failure) {
			error = describeError(failure);
		} finally {
			busy = false;
		}
	}

	async function remove() {
		if (!removing) return;
		await unwrap(api.DELETE('/api/v1/fields/{id}', { params: { path: { id: removing.id } } }));
		toast.success(m.master_deleted());
		await load();
	}

	const isChoice = $derived(
		editing === 'new' ? dataType === 'choice' : editing?.data_type === 'choice'
	);
</script>

<div class="flex flex-col gap-4">
	<div>
		<Button onclick={() => open('new')}>
			<Plus aria-hidden="true" />
			{m.master_add_field()}
		</Button>
	</div>
	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !fields}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if fields.length === 0}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-10 text-center text-muted-foreground"
		>
			{m.master_none()}
		</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each fields as field (field.id)}
				<li class="flex items-center gap-3 rounded-2xl border bg-card px-4 py-2">
					<div class="flex min-w-0 flex-1 flex-col">
						<span class="truncate">{field.name}</span>
						<span class="truncate text-sm text-muted-foreground">
							{typeLabel[field.data_type]()} · {scopeText(field)}
							{#if field.choices.length > 0}
								· {field.choices.join(', ')}{/if}
						</span>
					</div>
					<Button
						variant="ghost"
						size="icon"
						aria-label={m.master_rename({ name: field.name })}
						onclick={() => open(field)}
					>
						<Pencil aria-hidden="true" />
					</Button>
					<Button
						variant="ghost"
						size="icon"
						aria-label={m.master_delete({ name: field.name })}
						onclick={() => (removing = field)}
					>
						<Trash2 aria-hidden="true" />
					</Button>
				</li>
			{/each}
		</ul>
	{/if}
</div>

<Dialog.Root bind:open={() => editing !== null, (value) => !value && (editing = null)}>
	<Dialog.Content class="max-w-lg">
		<form class="flex flex-col gap-4" onsubmit={save} novalidate>
			<Dialog.Header>
				<Dialog.Title>{editing === 'new' ? m.master_add_field() : m.field_def_edit()}</Dialog.Title>
				<Dialog.Description>{m.field_def_hint()}</Dialog.Description>
			</Dialog.Header>
			<Field.Field>
				<Field.Label for="field-def-name">{m.field_name()}</Field.Label>
				<Input id="field-def-name" bind:value={name} maxlength={200} required />
			</Field.Field>
			<Field.Field>
				<Field.Label for="field-def-type">{m.field_def_type()}</Field.Label>
				<NativeSelect
					id="field-def-type"
					bind:value={dataType as string}
					disabled={editing !== 'new'}
					options={TYPES.map((type) => ({ value: type, label: typeLabel[type]() }))}
				/>
			</Field.Field>
			{#if isChoice}
				<Field.Field>
					<Field.Label for="field-def-choices">{m.field_def_choices()}</Field.Label>
					<Textarea id="field-def-choices" bind:value={choices} rows={4} />
					<Field.Description>{m.field_def_choices_hint()}</Field.Description>
				</Field.Field>
			{/if}
			<fieldset class="flex flex-col gap-2">
				<legend class="mb-1 text-sm font-medium">{m.field_def_scope()}</legend>
				<label class="flex items-center gap-2 text-sm">
					<input type="checkbox" bind:checked={global} class="size-4 accent-primary" />
					{m.field_def_scope_global()}
				</label>
				{#if !global}
					{#each types as type (type.id)}
						<label class="flex items-center gap-2 pl-6 text-sm">
							<input
								type="checkbox"
								value={type.id}
								bind:group={scope}
								class="size-4 accent-primary"
							/>
							{type.name}
						</label>
					{/each}
				{/if}
			</fieldset>
			{#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
			<Dialog.Footer>
				<Button type="button" variant="ghost" onclick={() => (editing = null)}>{m.cancel()}</Button>
				<Button type="submit" disabled={busy || name.trim() === ''}>{m.save()}</Button>
			</Dialog.Footer>
		</form>
	</Dialog.Content>
</Dialog.Root>

<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.master_delete({ name: removing?.name ?? '' })}
	description={m.master_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
