<script lang="ts">
	import ArrowDown from '@lucide/svelte/icons/arrow-down';
	import ArrowUp from '@lucide/svelte/icons/arrow-up';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import MultiPicker from '#lib/components/MultiPicker.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import ValueInput from '#lib/components/rules/ValueInput.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import type { Drawer, Lookup } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { ACTION_LABELS, PLACEHOLDER_LABELS } from '#lib/rules/describe.ts';
	import { TITLE_PLACEHOLDERS, defaultValue, type ActionNode } from '#lib/rules/model.ts';

	// One action. `drawers` are the ones the rule's owner may file into.
	let {
		action,
		lookup,
		drawers,
		error,
		onremove,
		onup,
		ondown
	}: {
		action: ActionNode;
		lookup: Lookup;
		drawers: readonly Drawer[];
		error?: string;
		onremove: () => void;
		onup?: () => void;
		ondown?: () => void;
	} = $props();

	const invalid = $derived(Boolean(error));
	const label = $derived(ACTION_LABELS[action.type]());
	let titleInput = $state<HTMLInputElement | null>(null);

	function options(list: readonly { id: string; name: string }[], value: string) {
		const known = value === '' || list.some((entry) => entry.id === value);
		return [
			{ value: '', label: m.rule_choose() },
			...list.map((entry) => ({ value: entry.id, label: entry.name })),
			...(known ? [] : [{ value, label: m.rule_unknown() }])
		];
	}

	const attribute = $derived(
		action.type === 'set_attribute'
			? (lookup.attributes.find((entry) => entry.id === action.attribute_id) ?? null)
			: null
	);

	/** Put `{name}` where the cursor is in the title template. */
	function insert(name: string) {
		if (action.type !== 'set_title') return;
		const text = `{${name}}`;
		const at = titleInput?.selectionStart ?? action.template.length;
		const end = titleInput?.selectionEnd ?? at;
		action.template = action.template.slice(0, at) + text + action.template.slice(end);
		const cursor = at + text.length;
		requestAnimationFrame(() => {
			titleInput?.focus();
			titleInput?.setSelectionRange(cursor, cursor);
		});
	}
</script>

<div class="flex flex-col gap-2 rounded-xl border bg-background p-3" data-key={action.key}>
	<div class="flex flex-wrap items-start gap-2">
		<span class="w-full pt-2 text-sm font-medium sm:w-44">{label}</span>
		<div class="flex min-w-0 flex-1 basis-64 flex-col gap-2">
			{#if action.type === 'set_drawer'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.drawer_id}
					options={options(drawers, action.drawer_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_contact'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.contact_id}
					options={options(lookup.contacts, action.contact_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_document_type'}
				<NativeSelect
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:value={action.document_type_id}
					options={options(lookup.documentTypes, action.document_type_id)}
					class="h-10"
				/>
			{:else if action.type === 'set_title'}
				<Input
					aria-label={label}
					aria-invalid={invalid || undefined}
					bind:ref={titleInput}
					bind:value={action.template}
				/>
				<div class="flex flex-wrap items-center gap-1.5 text-sm">
					<span class="text-muted-foreground">{m.rule_insert()}</span>
					{#each TITLE_PLACEHOLDERS as name (name)}
						<Button variant="outline" size="sm" class="h-7" onclick={() => insert(name)}>
							{PLACEHOLDER_LABELS[name]()}
						</Button>
					{/each}
				</div>
			{:else if action.type === 'add_tags' || action.type === 'remove_tags'}
				<MultiPicker
					{label}
					{invalid}
					options={lookup.tags.map((tag) => ({ value: tag.id, label: tag.name }))}
					selected={action.tag_ids}
					onchange={(next) => {
						if (action.type === 'add_tags' || action.type === 'remove_tags') action.tag_ids = next;
					}}
				/>
			{:else if action.type === 'set_attribute'}
				<NativeSelect
					aria-label={m.rule_condition_attribute()}
					aria-invalid={(invalid && !action.attribute_id) || undefined}
					value={action.attribute_id}
					options={options(lookup.attributes, action.attribute_id)}
					onchange={(id) => {
						if (action.type !== 'set_attribute') return;
						const chosen = lookup.attributes.find((entry) => entry.id === id) ?? null;
						action.attribute_id = id;
						action.value = (defaultValue('attribute', 'is', chosen) ?? '') as typeof action.value;
					}}
					class="h-10"
				/>
				{#if action.attribute_id}
					<ValueInput
						{attribute}
						{invalid}
						label={m.rule_condition_value()}
						value={action.value}
						onchange={(next) => {
							if (action.type === 'set_attribute') action.value = next as typeof action.value;
						}}
					/>
				{/if}
			{:else if action.type === 'force_review'}
				<Input
					aria-label={m.rule_review_reason()}
					placeholder={m.rule_review_reason()}
					aria-invalid={invalid || undefined}
					bind:value={action.reason}
				/>
			{/if}
		</div>
		<div class="flex gap-0.5">
			<Button
				variant="ghost"
				size="icon"
				aria-label={m.rule_move_up()}
				disabled={!onup}
				onclick={() => onup?.()}
			>
				<ArrowUp aria-hidden="true" />
			</Button>
			<Button
				variant="ghost"
				size="icon"
				aria-label={m.rule_move_down()}
				disabled={!ondown}
				onclick={() => ondown?.()}
			>
				<ArrowDown aria-hidden="true" />
			</Button>
			<Button variant="ghost" size="icon" aria-label={m.rule_remove()} onclick={onremove}>
				<Trash2 aria-hidden="true" />
			</Button>
		</div>
	</div>
	{#if error}<p class="text-sm text-destructive" role="alert">{error}</p>{/if}
</div>
