<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import Copy from '@lucide/svelte/icons/copy';
	import Pin from '@lucide/svelte/icons/pin';
	import ScanQrCode from '@lucide/svelte/icons/scan-qr-code';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import UserCheck from '@lucide/svelte/icons/user-check';
	import { untrack } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import CodeThumb from '#lib/components/document/CodeThumb.svelte';
	import SourceChip from '#lib/components/document/SourceChip.svelte';
	import NativeSelect from '#lib/components/NativeSelect.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { reportError } from '#lib/errors.ts';
	import {
		codeSpans,
		grouped,
		identifierKey,
		shown,
		type CodeFacts,
		type CodePlace,
		type Facts,
		type FoundIdentifier,
		type NamedContact,
		type Span
	} from '#lib/facts.ts';
	import { formatMoney } from '#lib/i18n.ts';
	import { kindLabel } from '#lib/identifiers.ts';
	import type { Lookup } from '#lib/masterdata.svelte.ts';
	import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { pathLabel, tagOptions } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// The facts beside the text: what supports the document's contact, what speaks of other
	// contacts, the identifiers of no contact (admins attach them, with a topic of the contact,
	// the document's by default), and the codes on the pages. Hovering or focusing an entry
	// marks it in the text; a click keeps the mark.
	let {
		facts,
		documentId,
		lookup,
		topicId = null,
		admin,
		onmark,
		onshowcode,
		onchanged
	}: {
		facts: Facts;
		documentId: string;
		lookup: Lookup | null;
		/** The document's topic: an identifier attached points to it, if the contact has it. */
		topicId?: string | null;
		/** Admins attach identifiers (they are master data). */
		admin: boolean;
		/** The spans to mark in the text, none to mark nothing; `reveal` after a click. */
		onmark: (spans: Span[], reveal: boolean) => void;
		/** Show a code where it stands on its page. */
		/** Show the code on one of its pages, all its places marked. */
		onshowcode: (code: CodeFacts, place: CodePlace) => void;
		/** After an identifier was attached. */
		onchanged: () => void;
	} = $props();

	type Entry = NamedContact | FoundIdentifier;

	let hover = $state<string | null>(null);
	let pinned = $state<string | null>(null);
	let attaching = $state<string | null>(null);
	let target = $state<'document' | 'other'>('document');
	let other = $state('');
	let attachTopic = $state('');
	let busy = $state(false);
	// A click keeps a mark: the text scrolls to it (also when the entry was marked already).
	let reveal = false;
	let clicks = $state(0);

	const groups = $derived(grouped(facts));
	const contacts = $derived(lookup?.contacts ?? []);
	const contactName = (id: string | null | undefined) =>
		contacts.find((contact) => contact.id === id)?.name ?? '…';
	const documentContact = $derived(facts.contact_id ? contactName(facts.contact_id) : null);
	const active = $derived(hover ?? pinned);
	// The topics of the contact an identifier goes to; the document's topic first of all.
	const attachContact = $derived(target === 'document' ? facts.contact_id : other);
	const attachTopics = $derived.by(() => {
		const enabled = contacts.find((contact) => contact.id === attachContact)?.topic_ids ?? [];
		return tagOptions(lookup?.tags ?? [], 'topic').filter((option) =>
			enabled.includes(option.value)
		);
	});
	$effect(() => {
		const options = attachTopics;
		untrack(() => {
			if (options.some((option) => option.value === attachTopic)) return;
			const own = options.some((option) => option.value === topicId);
			attachTopic = own && topicId ? topicId : '';
		});
	});
	const topicName = (id: string | null | undefined) => {
		const tag = lookup?.tags.find((entry) => entry.id === id);
		return tag ? pathLabel(tag) : null;
	};

	const entryId = (entry: Entry) =>
		'kind' in entry ? `id:${identifierKey(entry)}` : `name:${entry.contact_id}`;
	const codeId = (index: number) => `code:${index}`;

	$effect(() => {
		const id = active;
		void clicks;
		const scroll = reveal;
		reveal = false;
		if (id === null) {
			onmark([], false);
			return;
		}
		if (id.startsWith('code:')) {
			const code = facts.codes?.[Number(id.slice(5))];
			onmark(code ? codeSpans(facts, code) : [], scroll);
			return;
		}
		const entry = [...facts.named, ...facts.identifiers].find((item) => entryId(item) === id);
		onmark(entry?.spans ?? [], scroll);
	});

	function pin(id: string) {
		pinned = pinned === id ? null : id;
		reveal = pinned !== null;
		clicks += 1;
	}

	function startAttach(item: FoundIdentifier) {
		const id = entryId(item);
		attaching = attaching === id ? null : id;
		target = facts.contact_id ? 'document' : 'other';
		other = '';
		attachTopic = topicId ?? '';
	}

	async function attach(item: FoundIdentifier) {
		const contact = target === 'document' ? facts.contact_id : other;
		if (!contact) return;
		busy = true;
		try {
			await unwrap(
				api.POST('/api/v1/contacts/{id}/identifiers', {
					params: { path: { id: contact } },
					body: {
						kind: item.kind,
						value: item.value,
						...(attachTopic ? { topic_id: attachTopic } : {})
					}
				})
			);
			toast.success(
				m.facts_attach_done({ kind: kindLabel(item.kind), contact: contactName(contact) })
			);
			attaching = null;
			onchanged();
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}

	async function copy(text: string) {
		try {
			await navigator.clipboard.writeText(text);
			toast.success(m.content_copied());
		} catch (error) {
			reportError(error);
		}
	}

	const fieldName = (id: string) => lookup?.fields.find((field) => field.id === id)?.name ?? '…';
</script>

{#snippet entryRow(entry: Entry, contactLabel: string | null)}
	{@const id = entryId(entry)}
	{@const identifier = 'kind' in entry ? entry : null}
	{@const named = 'kind' in entry ? null : entry}
	<div
		class={cn('rounded-xl transition-colors', active === id && 'bg-panel')}
		onmouseenter={() => (hover = id)}
		onmouseleave={() => (hover = null)}
		role="presentation"
	>
		<div class="flex items-center gap-2 px-2.5 py-2">
			<button
				type="button"
				class="flex min-w-0 flex-1 flex-col items-start gap-0.5 text-left"
				aria-pressed={pinned === id}
				onclick={() => pin(id)}
				onfocus={() => (hover = id)}
				onblur={() => (hover = null)}
			>
				<span class="text-xs font-medium text-muted-foreground">
					{identifier
						? kindLabel(identifier.kind)
						: named?.alias
							? m.facts_alias()
							: m.facts_name()}
				</span>
				<span class={cn('text-sm break-words', identifier && 'font-mono text-[13px]')}>
					{identifier ? shown(identifier) : named?.name}
				</span>
				{#if identifier?.topic_id && topicName(identifier.topic_id)}
					<span class="text-xs text-muted-foreground">
						{m.facts_topic({ topic: topicName(identifier.topic_id) ?? '' })}
					</span>
				{/if}
				{#if contactLabel || entry.in_code}
					<span class="text-xs text-muted-foreground">
						{[
							contactLabel,
							entry.in_code ? (entry.in_text ? m.facts_in_code() : m.facts_only_in_code()) : null
						]
							.filter(Boolean)
							.join(' · ')}
					</span>
				{/if}
			</button>
			{#if identifier && !identifier.contact_id && admin}
				<Button
					variant="outline"
					size="sm"
					aria-expanded={attaching === id}
					onclick={() => startAttach(identifier)}
				>
					<Pin aria-hidden="true" />
					{m.facts_attach()}
				</Button>
			{/if}
		</div>
		{#if identifier && identifier.other_contacts}
			<p class="-mt-1 flex items-start gap-1.5 px-2.5 pb-2 text-xs text-lane-yellow-foreground">
				<TriangleAlert class="mt-px size-3.5 shrink-0" aria-hidden="true" />
				{identifier.other_contacts === 1
					? m.facts_other_contacts_one()
					: m.facts_other_contacts({ count: identifier.other_contacts })}
			</p>
		{/if}
		{#if identifier && attaching === id}
			<form
				class="mx-2.5 mb-2.5 flex flex-col gap-2.5 rounded-xl border bg-card p-3"
				onsubmit={(event) => {
					event.preventDefault();
					void attach(identifier);
				}}
			>
				<span class="text-sm font-medium">
					{m.facts_attach_title({ kind: kindLabel(identifier.kind) })}
				</span>
				{#if facts.contact_id}
					<label class="flex items-start gap-2.5 text-sm">
						<input
							type="radio"
							name="attach-target"
							value="document"
							bind:group={target}
							class="mt-1 accent-primary"
						/>
						<span class="flex flex-col">
							<span>{documentContact}</span>
							<span class="text-xs text-muted-foreground">{m.facts_attach_document_contact()}</span>
						</span>
					</label>
				{/if}
				<label class="flex items-center gap-2.5 text-sm">
					<input
						type="radio"
						name="attach-target"
						value="other"
						bind:group={target}
						class="accent-primary"
					/>
					{m.facts_attach_other()}
				</label>
				{#if target === 'other'}
					<NativeSelect
						bind:value={other}
						aria-label={m.facts_attach_other()}
						options={[
							{ value: '', label: m.facts_attach_pick() },
							...contacts.map((contact) => ({ value: contact.id, label: contact.name }))
						]}
					/>
				{/if}
				{#if attachTopics.length > 0}
					<label class="flex flex-col gap-1 text-sm">
						<span class="text-xs text-muted-foreground">{m.facts_attach_topic()}</span>
						<NativeSelect
							bind:value={attachTopic}
							aria-label={m.facts_attach_topic()}
							options={[{ value: '', label: m.topic_none() }, ...attachTopics]}
						/>
					</label>
				{/if}
				<span class="text-xs text-muted-foreground">{m.facts_attach_hint()}</span>
				<div class="flex justify-end gap-2">
					<Button type="button" variant="ghost" size="sm" onclick={() => (attaching = null)}>
						{m.cancel()}
					</Button>
					<Button
						type="submit"
						size="sm"
						disabled={busy || (target === 'other' ? !other : !facts.contact_id)}
					>
						{m.facts_attach()}
					</Button>
				</div>
			</form>
		{/if}
	</div>
{/snippet}

<div class="flex flex-col gap-3.5">
	<section aria-labelledby="facts-title" class="rounded-2xl border bg-card p-3.5">
		<div class="flex items-baseline gap-2 px-1 pb-1">
			<h2 id="facts-title" class="text-[15px] font-semibold">{m.facts_title()}</h2>
			<span class="text-xs text-muted-foreground">{m.facts_hint()}</span>
		</div>
		{#if groups.own.length + groups.others.length + groups.unknown.length === 0}
			<p class="px-1 py-2 text-sm text-muted-foreground">{m.facts_none()}</p>
		{/if}
		{#if groups.own.length > 0}
			<h3
				class="mt-2 flex items-center gap-1.5 px-1 text-xs font-semibold text-lane-green-foreground"
			>
				<UserCheck class="size-3.5" aria-hidden="true" />
				{m.facts_own()}
				<!-- Admins change the contact right here: its dialog opens over the page. -->
				{#if admin && facts.contact_id}
					{@const contactId = facts.contact_id}
					<button
						type="button"
						class="cursor-pointer underline-offset-2 hover:underline"
						title={m.facts_contact_open()}
						onclick={() => masterDataDialogs.open('contacts', contactId)}>{documentContact}</button
					>
				{:else}
					{documentContact}
				{/if}
			</h3>
			{#each groups.own as entry (entryId(entry))}
				{@render entryRow(entry, null)}
			{/each}
		{/if}
		{#if groups.others.length > 0}
			<h3 class="mt-3 px-1 text-xs font-semibold text-muted-foreground">{m.facts_others()}</h3>
			{#each groups.others as entry (entryId(entry))}
				{@render entryRow(entry, contactName(entry.contact_id))}
			{/each}
		{/if}
		{#if groups.unknown.length > 0}
			<h3 class="mt-3 px-1 text-xs font-semibold text-muted-foreground">{m.facts_unknown()}</h3>
			<p class="px-1 text-xs text-muted-foreground">
				{admin ? m.facts_unknown_hint() : m.facts_attach_admins()}
			</p>
			{#each groups.unknown as entry (entryId(entry))}
				{@render entryRow(entry, null)}
			{/each}
		{/if}
	</section>

	<section aria-labelledby="codes-title" class="rounded-2xl border bg-card p-3.5">
		<div class="flex items-baseline gap-2 px-1 pb-1">
			<h2 id="codes-title" class="flex items-center gap-1.5 text-[15px] font-semibold">
				<ScanQrCode class="size-4" aria-hidden="true" />
				{m.codes_title()}
			</h2>
			<span class="text-xs text-muted-foreground">{m.codes_hint()}</span>
		</div>
		{#if facts.codes === null}
			<p class="px-1 py-2 text-sm text-muted-foreground">{m.codes_unread()}</p>
		{:else if facts.codes.length === 0}
			<p class="px-1 py-2 text-sm text-muted-foreground">{m.codes_none()}</p>
		{:else}
			{#each facts.codes as code, index (index)}
				{@const id = codeId(index)}
				<div
					class={cn('flex gap-3 rounded-xl p-2.5 transition-colors', active === id && 'bg-panel')}
					onmouseenter={() => (hover = id)}
					onmouseleave={() => (hover = null)}
					role="presentation"
				>
					<button
						type="button"
						class="shrink-0 self-start rounded-lg"
						aria-label={m.codes_mark({ format: code.format })}
						aria-pressed={pinned === id}
						onclick={() => pin(id)}
						onfocus={() => (hover = id)}
						onblur={() => (hover = null)}
					>
						<CodeThumb {documentId} {code} />
					</button>
					<div class="flex min-w-0 flex-1 flex-col gap-1.5">
						<div class="flex flex-wrap items-center gap-2">
							<span class="text-sm font-medium">{code.giro ? m.code_giro() : code.format}</span>
							{#if code.giro}
								<span
									class="rounded-full bg-secondary px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
								>
									{m.code_giro_badge({ format: code.format })}
								</span>
							{/if}
							<!-- One code however often it is printed: a chip per page, as at a field. -->
							<span class="ml-auto flex flex-wrap justify-end gap-1">
								{#each code.places as place (place.page)}
									<SourceChip {place} onjump={() => onshowcode(code, place)} />
								{/each}
							</span>
						</div>
						{#if code.giro}
							<dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-3 gap-y-0.5 text-[13px]">
								<dt class="text-muted-foreground">{m.code_giro_name()}</dt>
								<dd>{code.giro.name}</dd>
								<dt class="text-muted-foreground">{m.identifier_kind_iban()}</dt>
								<dd class="font-mono text-xs break-words">
									{shown({ kind: 'iban', value: code.giro.iban })}
								</dd>
								{#if code.giro.bic}
									<dt class="text-muted-foreground">{m.code_giro_bic()}</dt>
									<dd class="font-mono text-xs">{code.giro.bic}</dd>
								{/if}
								{#if code.giro.amount}
									<dt class="text-muted-foreground">{m.code_giro_amount()}</dt>
									<dd class="flex flex-wrap items-center gap-1.5">
										{formatMoney(code.giro.amount.amount, code.giro.amount.currency)}
										{#each code.fields as field (field)}
											<span
												class="inline-flex items-center gap-1 rounded-full bg-lane-green px-2 py-0.5 text-[11px] font-medium text-lane-green-foreground"
											>
												<Check class="size-3" aria-hidden="true" />
												{m.code_same_as({ field: fieldName(field) })}
											</span>
										{/each}
									</dd>
								{/if}
								{#if code.giro.purpose}
									<dt class="text-muted-foreground">{m.code_giro_purpose()}</dt>
									<dd class="break-words">{code.giro.purpose}</dd>
								{/if}
							</dl>
						{:else}
							<div class="flex items-start gap-1 rounded-lg bg-surface py-1.5 pr-1 pl-2.5">
								<code
									class="max-h-24 min-w-0 flex-1 overflow-auto font-mono text-xs break-all whitespace-pre-wrap"
									>{code.text}</code
								>
								<Button
									variant="ghost"
									size="icon"
									class="size-7 shrink-0"
									aria-label={m.codes_copy()}
									onclick={() => copy(code.text)}
								>
									<Copy aria-hidden="true" />
								</Button>
							</div>
						{/if}
					</div>
				</div>
			{/each}
		{/if}
	</section>
</div>
