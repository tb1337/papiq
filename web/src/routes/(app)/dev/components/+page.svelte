<script lang="ts">
	import { toast } from 'svelte-sonner';
	import LaneBadge from '#lib/components/LaneBadge.svelte';
	import * as Alert from '#lib/components/ui/alert/index.ts';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Dialog from '#lib/components/ui/dialog/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { Skeleton } from '#lib/components/ui/skeleton/index.ts';
	import * as Table from '#lib/components/ui/table/index.ts';
	import { formatDate, formatMoney, formatNumber } from '#lib/i18n.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Sample data, not translated: it stands for user content.
	const rows = [
		{ title: 'Stadtwerke · Abschlag', date: new Date(2026, 9, 1), lane: 'green' as const },
		{ title: 'Hausrat · Beitrag 2027', date: new Date(2026, 8, 28), lane: 'yellow' as const },
		{ title: 'Finanzamt · Bescheid', date: new Date(2026, 8, 19), lane: 'red' as const },
		{ title: 'scan_0042.pdf', date: new Date(2026, 9, 8), lane: null }
	];
</script>

<svelte:head><title>{m.dev_components_title()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-10">
	<div class="flex flex-col gap-1">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.dev_components_title()}</h1>
		<p class="text-muted-foreground">{m.dev_components_hint()}</p>
	</div>

	<section class="flex flex-wrap items-center gap-3">
		<Button>{m.dev_sample_button()}</Button>
		<Button variant="secondary">{m.dev_sample_secondary()}</Button>
		<Button variant="outline">{m.dev_sample_button()}</Button>
		<Button variant="destructive">{m.dev_sample_destructive()}</Button>
		<Button size="lg">{m.dev_sample_button()}</Button>
		<Button size="sm" variant="ghost">{m.dev_sample_button()}</Button>
	</section>

	<section class="flex flex-wrap items-center gap-2">
		<LaneBadge lane="green" />
		<LaneBadge lane="yellow" />
		<LaneBadge lane="red" />
		<LaneBadge lane={null} />
	</section>

	<section class="flex max-w-sm flex-col gap-4">
		<Field.Field>
			<Field.Label for="sample">{m.dev_sample_input()}</Field.Label>
			<Input id="sample" />
		</Field.Field>
		<Alert.Root>
			<Alert.Title>{m.error_title()}</Alert.Title>
			<Alert.Description>{m.error_network()}</Alert.Description>
		</Alert.Root>
		<div class="flex flex-col gap-2">
			<Skeleton class="h-4 w-3/4" />
			<Skeleton class="h-4 w-1/2" />
		</div>
	</section>

	<section class="flex flex-wrap gap-3">
		<Dialog.Root>
			<Dialog.Trigger>
				{#snippet child({ props })}
					<Button variant="outline" {...props}>{m.dev_sample_open_dialog()}</Button>
				{/snippet}
			</Dialog.Trigger>
			<Dialog.Content>
				<Dialog.Header>
					<Dialog.Title>{m.dev_sample_dialog_title()}</Dialog.Title>
					<Dialog.Description>{m.dev_sample_dialog_text()}</Dialog.Description>
				</Dialog.Header>
			</Dialog.Content>
		</Dialog.Root>
		<Button variant="outline" onclick={() => toast.success(m.dev_sample_toast())}>
			{m.dev_sample_show_toast()}
		</Button>
	</section>

	<section class="flex flex-col gap-2 text-sm text-muted-foreground">
		<span>{formatDate(new Date())}</span>
		<span>{formatNumber(1234567.891)}</span>
		<span>{formatMoney('1234.50', 'EUR')}</span>
	</section>

	<section class="rounded-2xl border bg-card">
		<Table.Root>
			<Table.Header>
				<Table.Row>
					<Table.Head>{m.dev_sample_table_title()}</Table.Head>
					<Table.Head>{m.dev_sample_table_date()}</Table.Head>
					<Table.Head>{m.dev_sample_table_lane()}</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body>
				{#each rows as row (row.title)}
					<Table.Row>
						<Table.Cell class="font-medium">{row.title}</Table.Cell>
						<Table.Cell class="font-mono text-muted-foreground">{formatDate(row.date)}</Table.Cell>
						<Table.Cell><LaneBadge lane={row.lane} /></Table.Cell>
					</Table.Row>
				{/each}
			</Table.Body>
		</Table.Root>
	</section>
</div>
