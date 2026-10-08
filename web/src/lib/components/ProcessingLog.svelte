<script lang="ts">
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import * as Table from '#lib/components/ui/table/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { events } from '#lib/events.svelte.ts';
	import { formatDate, formatNumber } from '#lib/i18n.ts';
	import { m } from '#lib/paraglide/messages.js';

	type Step = components['schemas']['Step'];
	type Outcome = components['schemas']['Outcome'];

	// What each step did: result, confidence, reason, model and duration.
	let { documentId }: { documentId: string } = $props();

	let entries = $state<components['schemas']['LogEntry'][] | null>(null);
	let problem = $state<string | null>(null);

	const stepLabel: Record<Step, () => string> = {
		receive: m.step_receive,
		ocr: m.step_ocr,
		parse: m.step_parse,
		classify: m.step_classify,
		extract_attributes: m.step_extract_attributes,
		apply_rules: m.step_apply_rules,
		file: m.step_file
	};
	const outcomeLabel: Record<Outcome, () => string> = {
		ok: m.outcome_ok,
		uncertain: m.outcome_uncertain,
		failed: m.outcome_failed
	};

	async function load() {
		try {
			entries = await unwrap(
				api.GET('/api/v1/documents/{id}/log', { params: { path: { id: documentId } } })
			);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	$effect(() => {
		void documentId;
		void events.generation;
		void load();
		return events.subscribe((event) => {
			if (event.document_id === documentId && event.type === 'document.step_completed') void load();
		});
	});
</script>

{#if problem}
	<p class="text-destructive" role="alert">{problem}</p>
{:else if entries === null}
	<p class="text-muted-foreground">{m.loading()}</p>
{:else if entries.length === 0}
	<p class="text-muted-foreground">{m.log_none()}</p>
{:else}
	<Table.Root>
		<Table.Header>
			<Table.Row>
				<Table.Head>{m.log_step()}</Table.Head>
				<Table.Head>{m.log_outcome()}</Table.Head>
				<Table.Head>{m.log_confidence()}</Table.Head>
				<Table.Head>{m.log_reason()}</Table.Head>
				<Table.Head>{m.log_model()}</Table.Head>
				<Table.Head>{m.log_duration()}</Table.Head>
			</Table.Row>
		</Table.Header>
		<Table.Body>
			{#each entries as entry (`${entry.run}-${entry.step}-${entry.started_at}`)}
				<Table.Row>
					<Table.Cell>
						{stepLabel[entry.step]()}
						<span class="block text-xs text-muted-foreground">
							{m.log_run({ run: entry.run })} · {formatDate(entry.started_at, {
								dateStyle: 'short',
								timeStyle: 'short'
							})}
						</span>
					</Table.Cell>
					<Table.Cell>{outcomeLabel[entry.outcome]()}</Table.Cell>
					<Table.Cell>
						{entry.confidence === null
							? '–'
							: formatNumber(entry.confidence, { style: 'percent', maximumFractionDigits: 0 })}
					</Table.Cell>
					<Table.Cell class="max-w-xs whitespace-normal">{entry.reason ?? '–'}</Table.Cell>
					<Table.Cell>{entry.model_version ?? '–'}</Table.Cell>
					<Table.Cell
						>{formatNumber(entry.duration_ms / 1000, {
							style: 'unit',
							unit: 'second',
							unitDisplay: 'narrow',
							maximumFractionDigits: 1
						})}</Table.Cell
					>
				</Table.Row>
			{/each}
		</Table.Body>
	</Table.Root>
{/if}
