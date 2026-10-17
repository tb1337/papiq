<script lang="ts" module>
	import FolderTree from '@lucide/svelte/icons/folder-tree';
	import Layers from '@lucide/svelte/icons/layers';
	import TagIcon from '@lucide/svelte/icons/tag';
	import Users from '@lucide/svelte/icons/users';
	import type { TagKind } from '#lib/masterdata-api.ts';
	import { m } from '#lib/paraglide/messages.js';

	/** Each kind's section: icon, name, what it is for. */
	export const SECTIONS: Record<TagKind, { icon: typeof TagIcon; about: () => string }> = {
		tags: { icon: TagIcon, about: m.tag_kind_document_about },
		'contact-groups': { icon: Users, about: m.tag_kind_contact_about },
		'type-groups': { icon: Layers, about: m.tag_kind_type_about },
		topics: { icon: FolderTree, about: m.tag_kind_topic_about }
	};
</script>

<script lang="ts">
	import ChevronDown from '@lucide/svelte/icons/chevron-down';
	import ChevronRight from '@lucide/svelte/icons/chevron-right';
	import Plus from '@lucide/svelte/icons/plus';
	import { SvelteSet } from 'svelte/reactivity';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import { BASE } from '#lib/base.ts';
	import MasterRow, { type Usage } from '#lib/components/MasterRow.svelte';
	import TagChip from '#lib/components/TagChip.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
	import { KINDS, TAG_KINDS } from '#lib/masterdata-api.ts';
	import type { DocumentType, Tag } from '#lib/masterdata.svelte.ts';
	import { phone } from '#lib/phone.svelte.ts';
	import { scope } from '#lib/scope.svelte.ts';
	import {
		childCounts,
		kindFilter,
		rememberCollapsed,
		rememberedCollapsed,
		searched,
		TAG_KIND_ORDER
	} from '#lib/tag-kinds.ts';
	import { below, ofScope, visibleRows } from '#lib/tags.ts';
	import { cn } from '#lib/utils.ts';

	// Every kind of tag in one place: a filter by kind (in the URL), one section per kind with
	// its tree (sections collapse, remembered in this browser), the search of the page over all
	// of them. Each tag as its chip with how it is set and shown, how often it is used (as a
	// link), and the editor; on a phone the row opens it.
	let { query = '' }: { query?: string } = $props();

	let tags = $state<Tag[] | null>(null);
	let types = $state<DocumentType[]>([]);
	let problem = $state<string | null>(null);
	const collapsedKinds = new SvelteSet<TagKind>(rememberedCollapsed());
	const collapsedTags = new SvelteSet<string>();
	const NO_COLLAPSE: ReadonlySet<string> = new Set();

	const filter = $derived(kindFilter(page.url.searchParams));
	const kinds = $derived(filter ? [filter] : TAG_KIND_ORDER);
	const searching = $derived(query.trim() !== '');
	// A search or a filter by kind shows its sections open, whatever this browser keeps
	// collapsed (a lone collapsed section would show nothing).
	const forcedOpen = $derived(searching || filter !== null);
	const childCount = $derived(childCounts(tags ?? []));
	const sections = $derived(
		kinds.map((kind) => {
			const own = searched(ofScope(tags ?? [], TAG_KINDS[kind]), query);
			return {
				kind,
				rows: visibleRows(own, searching ? NO_COLLAPSE : collapsedTags),
				open: forcedOpen || !collapsedKinds.has(kind)
			};
		})
	);
	// A search shows only the sections it finds something in.
	const shown = $derived(
		searching ? sections.filter((section) => section.rows.length > 0) : sections
	);

	async function load() {
		try {
			[tags, types] = await Promise.all([
				unwrap(
					api.GET('/api/v1/tags', {
						params: { query: { document_counts: true, all_users: scope.query } }
					})
				),
				unwrap(api.GET('/api/v1/document-types'))
			]);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	// Again after a dialog saved (wherever it was opened), and with another scope.
	$effect(() => {
		void masterDataDialogs.changed;
		void scope.allUsers;
		void load();
	});

	function choose(kind: TagKind | null) {
		void goto(kind ? `?tab=tags&kind=${kind}` : '?tab=tags', { reset: false });
	}

	function toggleKind(kind: TagKind) {
		if (collapsedKinds.has(kind)) collapsedKinds.delete(kind);
		else collapsedKinds.add(kind);
		rememberCollapsed(collapsedKinds);
	}

	function toggleTag(id: string) {
		if (collapsedTags.has(id)) collapsedTags.delete(id);
		else collapsedTags.add(id);
	}

	/** How a tag is set and whether documents show it, where it is not the default of its kind:
	 * a document tag or topic set by hand only or hidden, a group shown. */
	function hints(tag: Tag): string[] {
		const short = phone.current;
		const group = tag.scope === 'contact' || tag.scope === 'document_type';
		const found: (string | null)[] = [
			!group && tag.assignment === 'manual'
				? short
					? m.tag_manual_short()
					: m.tag_assignment_manual()
				: null,
			!group && !tag.shown ? (short ? m.tag_hidden_short() : m.tag_hidden()) : null,
			group && tag.shown ? (short ? m.tag_shown_short() : m.tag_shown_badge()) : null
		];
		return found.filter((hint) => hint !== null);
	}

	const MASTER = `${BASE}/admin/master-data`;

	/** What uses a tag, linked to where it is listed: documents; a contact group's contacts; a
	 * type group's types; a topic's contacts and documents. */
	function usage(kind: TagKind, tag: Tag): Usage[] {
		const documents = {
			count: tag.document_count,
			href: `${BASE}/documents?${KINDS[kind].filter}=${tag.id}`
		};
		if (kind === 'contact-groups') {
			return [
				{
					count: tag.contact_count,
					href: `${MASTER}?tab=contacts&group=${tag.id}`,
					unit: 'contacts'
				}
			];
		}
		if (kind === 'type-groups') {
			return [
				{
					count: tag.document_type_count,
					href: `${MASTER}?tab=document-types&group=${tag.id}`,
					unit: 'types'
				}
			];
		}
		if (kind === 'topics') {
			return [
				{
					count: tag.contact_count,
					href: `${MASTER}?tab=contacts&topic=${tag.id}`,
					unit: 'contacts'
				},
				documents
			];
		}
		return [documents];
	}

	/** The types in a type group or one below it. */
	function members(tag: Tag): string {
		const group = below(tags ?? [], tag.id).add(tag.id);
		return types
			.filter((type) => (type.group_ids ?? []).some((id) => group.has(id)))
			.map((type) => type.name)
			.join(', ');
	}
</script>

<div class="flex flex-col gap-3 sm:gap-4">
	<!-- On a phone the chips scroll sideways in their row; the page itself never does. -->
	<div
		role="group"
		aria-label={m.tag_kinds_label()}
		class="-mx-4 flex gap-2 overflow-x-auto px-4 pb-1 sm:mx-0 sm:flex-wrap sm:px-0 sm:pb-0"
	>
		{#each [null, ...TAG_KIND_ORDER] as kind (kind ?? 'all')}
			<button
				type="button"
				aria-pressed={filter === kind}
				class={cn(
					'h-9 shrink-0 rounded-full border px-3.5 text-sm whitespace-nowrap transition-colors max-sm:h-11',
					filter === kind
						? 'border-primary bg-primary/10 font-medium text-foreground'
						: 'border-input bg-card hover:bg-secondary'
				)}
				onclick={() => choose(kind)}
			>
				{kind ? KINDS[kind].title() : m.tag_kinds_all()}
			</button>
		{/each}
	</div>

	{#if problem}
		<p class="text-destructive" role="alert">{problem}</p>
	{:else if !tags}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if shown.length === 0}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-10 text-center text-muted-foreground"
		>
			{m.assignment_none_found()}
		</p>
	{:else}
		{#each shown as { kind, rows, open } (kind)}
			{@const Icon = SECTIONS[kind].icon}
			{@const title = KINDS[kind].title()}
			<section
				aria-labelledby="tags-{kind}"
				class="overflow-hidden rounded-2xl border bg-card"
				data-kind={kind}
			>
				<div
					class="flex min-h-12 items-center gap-2 bg-muted/60 py-1 pr-1.5 pl-1 sm:gap-2.5 sm:pr-2.5"
				>
					{#if forcedOpen}
						<span class="size-11 shrink-0 sm:size-8" aria-hidden="true"></span>
					{:else}
						<Button
							variant="ghost"
							size="icon"
							class="size-11 shrink-0 aria-expanded:bg-transparent sm:size-8"
							aria-expanded={open}
							aria-controls={open ? `tags-${kind}-list` : undefined}
							aria-label={open ? m.tree_collapse({ name: title }) : m.tree_expand({ name: title })}
							onclick={() => toggleKind(kind)}
						>
							{#if open}<ChevronDown aria-hidden="true" />{:else}<ChevronRight
									aria-hidden="true"
								/>{/if}
						</Button>
					{/if}
					{#if !phone.current}<Icon class="size-4 shrink-0 text-brand" aria-hidden="true" />{/if}
					<h2 id="tags-{kind}" class="text-sm font-semibold whitespace-nowrap">{title}</h2>
					<span class="min-w-0 flex-1 truncate text-sm text-muted-foreground">
						{#if !phone.current}{SECTIONS[kind].about()}{/if}
					</span>
					<Button
						variant={phone.current ? 'ghost' : 'outline'}
						size="icon"
						class="size-11 shrink-0 rounded-full text-brand sm:size-8"
						aria-label={KINDS[kind].add()}
						title={KINDS[kind].add()}
						onclick={() => masterDataDialogs.open(kind)}
					>
						<Plus aria-hidden="true" />
					</Button>
				</div>
				{#if open}
					{#if rows.length === 0}
						<p id="tags-{kind}-list" class="border-t px-4 py-3 text-sm text-muted-foreground">
							{m.master_none()}
						</p>
					{:else}
						<ul id="tags-{kind}-list" class="divide-y border-t" aria-label={title}>
							{#each rows as { tag, depth, hasChildren } (tag.id)}
								<MasterRow
									onopen={() => masterDataDialogs.open(kind, tag)}
									editLabel={m.master_edit({ name: tag.name })}
									usage={usage(kind, tag)}
								>
									{#snippet before()}
										<!-- One level further in per level; chips of tags without a chevron line up
										     with those of tags with one. -->
										<span
											class="w-[calc(var(--depth)*1.5rem)] shrink-0 sm:w-[calc(var(--depth)*2.25rem)]"
											style="--depth: {depth}"
											data-depth={depth}
											aria-hidden="true"
										></span>
										{#if hasChildren && !searching}
											<Button
												variant="ghost"
												size="icon"
												class="size-11 shrink-0 aria-expanded:bg-transparent sm:size-8"
												aria-expanded={!collapsedTags.has(tag.id)}
												aria-label={collapsedTags.has(tag.id)
													? m.tree_expand({ name: tag.name })
													: m.tree_collapse({ name: tag.name })}
												onclick={() => toggleTag(tag.id)}
											>
												{#if collapsedTags.has(tag.id)}
													<ChevronRight aria-hidden="true" />
												{:else}
													<ChevronDown aria-hidden="true" />
												{/if}
											</Button>
										{:else}
											<span class="size-11 shrink-0 sm:size-8" aria-hidden="true"></span>
										{/if}
									{/snippet}
									<span class="flex max-w-full min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
										<TagChip
											tag={{ ...tag, path: [tag.name] }}
											topic={tag.scope === 'topic'}
											group={tag.scope === 'contact' || tag.scope === 'document_type'}
										/>
										{#each hints(tag) as hint (hint)}
											<span
												class="rounded-full bg-secondary px-2 text-xs leading-5 text-muted-foreground"
											>
												{hint}
											</span>
										{/each}
										{#if !phone.current && hasChildren}
											<span class="text-sm text-muted-foreground">
												{m.tag_below({ count: childCount.get(tag.id) ?? 0 })}
											</span>
										{/if}
										{#if !phone.current && kind === 'type-groups' && members(tag)}
											<span class="min-w-0 truncate text-sm text-muted-foreground"
												>{members(tag)}</span
											>
										{/if}
									</span>
								</MasterRow>
							{/each}
						</ul>
					{/if}
				{/if}
			</section>
		{/each}
	{/if}
</div>
