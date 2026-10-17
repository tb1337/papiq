<script lang="ts">
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Plus from '@lucide/svelte/icons/plus';
	import X from '@lucide/svelte/icons/x';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import TagChip from '#lib/components/TagChip.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import {
		IDENTIFIER_KINDS,
		type IdentifierRow,
		type IdentifierSource,
		kindLabel,
		row
	} from '#lib/identifiers.ts';
	import type { Tag } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { phone } from '#lib/phone.svelte.ts';
	import { pathLabel } from '#lib/tags.ts';

	// A contact's identifiers: those entered by hand can be changed and removed, and point to
	// one of the contact's `topics` (or none); learned ones can be taken over (then they are
	// entered by hand) or rejected (training does not learn them again); a rejected one can be
	// allowed again. On a phone each is a row (kind, value, topic) that opens in place.
	let { rows = $bindable(), topics = [] }: { rows: IdentifierRow[]; topics?: readonly Tag[] } =
		$props();

	const kinds = IDENTIFIER_KINDS.map((kind) => ({ value: kind, label: kindLabel(kind) }));
	const topicOptions = $derived([
		{ value: '', label: m.topic_none() },
		...topics.map((topic) => ({ value: topic.id, label: pathLabel(topic) }))
	]);
	// The row open on a phone.
	let editing = $state<number | null>(null);

	function topicOf(id: string): Tag | null {
		return topics.find((topic) => topic.id === id) ?? null;
	}

	function become(item: IdentifierRow, source: IdentifierSource) {
		rows = rows.map((other) => (other.key === item.key ? { ...other, source } : other));
	}

	function drop(item: IdentifierRow) {
		rows = rows.filter((other) => other.key !== item.key);
		editing = null;
	}

	function add() {
		const fresh = row();
		rows = [...rows, fresh];
		editing = fresh.key;
	}
</script>

{#snippet manualFields(item: IdentifierRow, index: number, stacked: boolean)}
	<NativeSelect
		bind:value={rows[index].kind}
		options={kinds}
		aria-label={m.identifier_kind()}
		class={stacked ? 'w-full' : 'w-44 shrink-0'}
	/>
	<Input
		bind:value={rows[index].value}
		aria-label={m.identifier_value()}
		maxlength={100}
		class={stacked ? 'w-full font-mono' : 'min-w-0 flex-1'}
	/>
	{#if topics.length > 0}
		<NativeSelect
			bind:value={rows[index].topic_id}
			options={topicOf(item.topic_id) || item.topic_id === ''
				? topicOptions
				: [...topicOptions, { value: item.topic_id, label: m.topic_not_enabled() }]}
			aria-label={m.identifier_topic()}
			class={stacked ? 'w-full' : 'order-last w-full'}
		/>
	{/if}
{/snippet}

{#snippet learnedActions(item: IdentifierRow)}
	{#if item.source === 'learned'}
		<Button type="button" variant="ghost" size="sm" onclick={() => become(item, 'manual')}>
			{m.identifier_keep()}
		</Button>
		<Button type="button" variant="ghost" size="sm" onclick={() => become(item, 'rejected')}>
			{m.identifier_reject()}
		</Button>
	{:else}
		<Button type="button" variant="ghost" size="sm" onclick={() => drop(item)}>
			{m.identifier_unblock()}
		</Button>
	{/if}
{/snippet}

{#if phone.current}
	<ul class="divide-y overflow-hidden rounded-2xl border bg-card">
		{#each rows as item, index (item.key)}
			{@const topic = topicOf(item.topic_id)}
			<li>
				{#if editing === item.key}
					<div class="flex flex-col gap-2 p-3">
						{#if item.source === 'manual'}
							{@render manualFields(item, index, true)}
							<div class="flex items-center justify-between gap-2">
								<Button
									type="button"
									variant="ghost"
									size="sm"
									class="text-destructive"
									onclick={() => drop(item)}
								>
									<X aria-hidden="true" />
									{m.identifier_remove()}
								</Button>
								<Button type="button" variant="outline" size="sm" onclick={() => (editing = null)}>
									{m.picker_done()}
								</Button>
							</div>
						{:else}
							<span class="text-sm break-all">
								{kindLabel(item.kind)}:&nbsp;<span class="font-mono">{item.value}</span>
							</span>
							<div class="flex flex-wrap items-center gap-1">
								{@render learnedActions(item)}
								<Button
									type="button"
									variant="outline"
									size="sm"
									class="ml-auto"
									onclick={() => (editing = null)}
								>
									{m.picker_done()}
								</Button>
							</div>
						{/if}
					</div>
				{:else}
					<button
						type="button"
						class="flex min-h-14 w-full items-center gap-2.5 py-2 pr-3 pl-3.5 text-left hover:bg-secondary/60"
						onclick={() => (editing = item.key)}
					>
						<span class="flex min-w-0 flex-1 flex-col gap-0.5">
							<span class="text-xs text-muted-foreground">{kindLabel(item.kind)}</span>
							<span
								class={[
									'font-mono text-sm break-all',
									item.source === 'rejected' && 'text-muted-foreground line-through'
								]}
							>
								{item.value || m.value_unset()}
							</span>
						</span>
						{#if topic}<TagChip tag={topic} topic size="sm" />{/if}
						{#if item.source !== 'manual'}
							<Badge variant={item.source === 'learned' ? 'secondary' : 'outline'}>
								{item.source === 'learned' ? m.identifier_learned() : m.identifier_rejected()}
							</Badge>
						{/if}
						<ChevronRight class="size-4 shrink-0 text-muted-foreground" aria-hidden="true" />
					</button>
				{/if}
			</li>
		{/each}
		<li>
			<button
				type="button"
				class="flex min-h-12 w-full items-center gap-2 px-3.5 text-left text-brand hover:bg-secondary/60"
				onclick={add}
			>
				<Plus class="size-4" aria-hidden="true" />
				{m.identifier_add()}
			</button>
		</li>
	</ul>
{:else}
	<div class="flex flex-col gap-2">
		{#each rows as item, index (item.key)}
			{#if item.source === 'manual'}
				<!-- Kind, value and removal on a line, the topic below them. -->
				<div class={['flex flex-wrap items-center gap-2', topics.length > 0 && 'pb-1']}>
					{@render manualFields(item, index, false)}
					<Button
						type="button"
						variant="ghost"
						size="icon"
						aria-label={m.identifier_remove()}
						onclick={() => drop(item)}
					>
						<X aria-hidden="true" />
					</Button>
				</div>
			{:else}
				<div class="flex flex-wrap items-center gap-2 rounded-xl border border-dashed px-3 py-1.5">
					<span
						class={[
							// Readable whole: on a narrow screen the actions go to the next line.
							'min-w-60 flex-1 text-sm break-all',
							item.source === 'rejected' && 'text-muted-foreground line-through'
						]}
					>
						{kindLabel(item.kind)}:&nbsp;<span class="font-mono">{item.value}</span>
						{#if item.source === 'learned' && topicOf(item.topic_id)}
							<span class="text-muted-foreground"> · {pathLabel(topicOf(item.topic_id)!)}</span>
						{/if}
					</span>
					<Badge variant={item.source === 'learned' ? 'secondary' : 'outline'}>
						{item.source === 'learned' ? m.identifier_learned() : m.identifier_rejected()}
					</Badge>
					{@render learnedActions(item)}
				</div>
			{/if}
		{/each}
		<div>
			<Button type="button" variant="outline" size="sm" onclick={add}>
				<Plus aria-hidden="true" />
				{m.identifier_add()}
			</Button>
		</div>
	</div>
{/if}
