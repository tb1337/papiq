<script lang="ts">
	import ArrowLeft from '@lucide/svelte/icons/arrow-left';
	import { page } from '$app/state';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Table from '#lib/components/ui/table/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { formatDate } from '#lib/i18n.ts';
	import { PagedList } from '#lib/paging.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';

	type Outcome = components['schemas']['DeliveryOutcome'];
	const id = $derived(page.params.id ?? '');
	let name = $state('');

	const list = new PagedList<components['schemas']['DeliveryOut']>(async (before) => {
		const items = await unwrap(
			api.GET('/api/v1/webhooks/{id}/deliveries', {
				params: {
					path: { id },
					query: { before: (before as string | null) ?? undefined, limit: 50 }
				}
			})
		);
		// A full page may have more behind it; the next one starts after its last entry.
		return { items, next: items.length === 50 ? items[items.length - 1].id : null };
	});

	$effect(() => {
		void id;
		void list.reload();
		api
			.GET('/api/v1/webhooks/{id}', { params: { path: { id } } })
			.then(({ data }) => (name = data?.name ?? ''));
	});

	const outcomeLabel: Record<Outcome, () => string> = {
		delivered: m.delivery_delivered,
		retrying: m.delivery_retrying,
		gave_up: m.delivery_gave_up,
		dropped: m.delivery_dropped
	};
</script>

<svelte:head><title>{m.webhook_log_title()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<a
		href="{BASE}/webhooks"
		class="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground"
	>
		<ArrowLeft class="size-4" aria-hidden="true" />
		{m.nav_webhooks()}
	</a>
	<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.webhook_log_title()} {name}</h1>

	{#if list.error}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{describeError(list.error)}</p>
			<Button variant="outline" onclick={() => list.reload()}>{m.retry()}</Button>
		</div>
	{:else if list.empty}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{m.delivery_none()}
		</p>
	{:else}
		<Table.Root>
			<Table.Header>
				<Table.Row>
					<Table.Head>{m.delivery_time()}</Table.Head>
					<Table.Head>{m.delivery_event()}</Table.Head>
					<Table.Head>{m.log_outcome()}</Table.Head>
					<Table.Head>{m.delivery_status()}</Table.Head>
					<Table.Head>{m.log_duration()}</Table.Head>
					<Table.Head>{m.delivery_error()}</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each list.items as delivery (delivery.id)}
					<Table.Row>
						<Table.Cell>
							{formatDate(delivery.started_at, { dateStyle: 'short', timeStyle: 'medium' })}
						</Table.Cell>
						<Table.Cell>
							{delivery.event_type}
							<span class="block text-xs text-muted-foreground">
								{m.delivery_attempt({ attempt: delivery.attempt })}
							</span>
						</Table.Cell>
						<Table.Cell>
							<Badge variant={delivery.outcome === 'delivered' ? 'secondary' : 'outline'}>
								{outcomeLabel[delivery.outcome]()}
							</Badge>
							{#if delivery.next_attempt_at}
								<span class="block text-xs text-muted-foreground">
									{m.delivery_next({
										time: formatDate(delivery.next_attempt_at, {
											timeStyle: 'short',
											dateStyle: 'short'
										})
									})}
								</span>
							{/if}
						</Table.Cell>
						<Table.Cell>{delivery.status_code ?? '–'}</Table.Cell>
						<Table.Cell>{`${delivery.duration_ms} ms`}</Table.Cell>
						<Table.Cell class="max-w-xs whitespace-normal">{delivery.error ?? '–'}</Table.Cell>
					</Table.Row>
				{/each}
			</Table.Body>
		</Table.Root>
		{#if list.hasMore}
			<div class="flex justify-center">
				<Button variant="outline" disabled={list.loading} onclick={() => list.more()}>
					{m.load_more()}
				</Button>
			</div>
		{/if}
	{/if}
</div>
