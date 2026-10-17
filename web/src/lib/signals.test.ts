import { describe, expect, it } from 'vitest';
import type { components } from '#lib/api/schema.ts';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { signalSummary } from '#lib/signals.ts';

type LogEntry = components['schemas']['LogEntry'];

const UTILITY = '0199a6c4-0000-7000-8000-0000000000c1';
const INVOICE = '0199a6c4-0000-7000-8000-0000000000e1';
const lookup = {
	contacts: [{ id: UTILITY, name: 'Stadtwerke' }],
	documentTypes: [{ id: INVOICE, name: 'Rechnung' }],
	tags: [],
	spaces: [],
	users: []
} as unknown as Lookup;

const entry = (node: string, output: Record<string, unknown>) =>
	({ node, output }) as unknown as LogEntry;

const texts = (rows: ReturnType<typeof signalSummary>) =>
	rows.map((row) => [row.label, row.items.map((item) => item.text)]);

describe('signalSummary', () => {
	it('tells how well the OCR read each page it recognised', () => {
		const readings = [
			{ page: 1, words: 224, unsure: 23 },
			{ page: 2, words: 400, unsure: 8 }
		];
		const rows = texts(signalSummary(entry('ocr', { readings }), lookup));
		expect(rows).toEqual([
			[
				m.log_reading(),
				[
					m.log_reading_page({ page: '1', unsure: '10%', words: '224' }),
					m.log_reading_page({ page: '2', unsure: '2%', words: '400' })
				]
			]
		]);
		expect(signalSummary(entry('ocr', { pages: 1 }), lookup)).toEqual([]);
	});

	it('tells why a signal abstained', () => {
		const rows = signalSummary(entry('signal_classifier', { abstained: 'no model' }), lookup);
		expect(texts(rows)).toEqual([[m.signal_abstained(), ['no model']]]);
	});

	it('shows the language model’s proposal', () => {
		const proposal = {
			sender: { value: 'Stadtwerke GmbH', evidence: null },
			recipient: null,
			document_type: null,
			new_document_type: 'Abschlag',
			document_date: { value: '31.03.2026', evidence: null },
			title: 'Strom März',
			language: 'de'
		};
		const rows = texts(signalSummary(entry('signal_llm', { proposal }), lookup));
		expect(rows).toEqual([
			[m.signal_sender(), ['Stadtwerke GmbH']],
			[m.signal_recipient(), [m.signal_none()]],
			[m.field_document_type(), [`Abschlag (${m.candidate_new()})`]],
			[m.field_date(), ['31.03.2026']],
			[m.field_title(), ['Strom März']],
			[m.signal_language(), ['de']]
		]);
	});

	it('names the classifier’s best classes, the similar documents and the facts', () => {
		const classifier = signalSummary(
			entry('signal_classifier', {
				model: 2,
				contacts: [[UTILITY, 0.91]],
				document_types: [[INVOICE, 0.5]]
			}),
			lookup
		);
		expect(texts(classifier)).toEqual([
			[m.signal_contacts(), ['Stadtwerke 91%']],
			[m.signal_document_types(), ['Rechnung 50%']]
		]);

		const [similar] = signalSummary(
			entry('signal_neighbours', {
				neighbours: [
					{ document_id: 'd1', contact_id: UTILITY, document_type_id: null, score: 0.8776 },
					{ document_id: 'd2', contact_id: null, document_type_id: null }
				]
			}),
			lookup
		);
		expect(similar.items).toEqual([
			{
				text: `${m.signal_document({ number: 1 })}: Stadtwerke · ${m.signal_similarity({ similarity: '0.88' })}`,
				href: '/ui/documents/d1'
			},
			{ text: m.signal_document({ number: 2 }), href: '/ui/documents/d2' }
		]);

		const facts = signalSummary(
			entry('signal_facts', {
				named: [{ contact_id: UTILITY, name: 'Stadtwerke', alias: false }],
				identifiers: [{ contact_id: UTILITY, kind: 'iban', value: 'DE89' }],
				unknown_identifiers: []
			}),
			lookup
		);
		expect(texts(facts)).toEqual([
			[m.signal_named(), ['Stadtwerke']],
			[m.signal_identifiers(), [`${m.identifier_kind_iban()} DE89: Stadtwerke`]],
			[m.signal_unknown_identifiers(), [m.signal_none()]]
		]);
		expect(signalSummary(entry('judge', {}), lookup)).toEqual([]);
	});
});

describe('the topic in the signals', () => {
	const TOPIC = '0199a6c4-0000-7000-8000-0000000000b1';
	const topics = {
		...lookup,
		tags: [{ id: TOPIC, name: 'Arbeit', path: ['Arbeit'], scope: 'topic' }]
	} as unknown as Lookup;

	it('shows the topic the model named, and the classifier’s best topics', () => {
		const proposal = { sender: { value: null, evidence: null }, topic: 'Arbeit' };
		const llm = texts(signalSummary(entry('signal_llm', { proposal }), topics));
		expect(llm).toContainEqual([m.field_topic(), ['Arbeit']]);
		const output = { contacts: [], document_types: [], topics: [[TOPIC, 0.93]] };
		const classifier = texts(signalSummary(entry('signal_classifier', output), topics));
		expect(classifier).toContainEqual([m.signal_topics(), ['Arbeit 93%']]);
	});
});
