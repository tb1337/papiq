/**
 * What a signal said in a run, for the protocol: the language model's proposal, the
 * classifier's best classes, the similar documents, the names and identifiers in the text, or
 * why it abstained. For the OCR, how well it read each page it recognised.
 */
import type { components } from '#lib/api/schema.ts';
import { documentHref } from '#lib/candidates.ts';
import { describeValue } from '#lib/describe.ts';
import { formatNumber } from '#lib/i18n.ts';
import { IDENTIFIER_KINDS, kindLabel, type IdentifierKind } from '#lib/identifiers.ts';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';

type LogEntry = components['schemas']['LogEntry'];

export const SIGNAL_NODES = [
	'signal_llm',
	'signal_classifier',
	'signal_neighbours',
	'signal_facts'
] as const;

export interface SummaryItem {
	text: string;
	href?: string;
}

export interface SummaryRow {
	label: string;
	items: SummaryItem[];
}

type Json = Record<string, unknown>;

const object = (value: unknown): Json =>
	value !== null && typeof value === 'object' && !Array.isArray(value) ? (value as Json) : {};
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : []);
const text = (value: unknown): string | null =>
	typeof value === 'string' && value.trim() !== '' ? value : null;
const percent = (value: unknown) =>
	formatNumber(typeof value === 'number' ? value : 0, {
		style: 'percent',
		maximumFractionDigits: 0
	});

function kind(value: unknown): string {
	const name = String(value);
	return (IDENTIFIER_KINDS as readonly string[]).includes(name)
		? kindLabel(name as IdentifierKind)
		: name;
}

function named(id: unknown, lookup: Lookup | null): string {
	return typeof id === 'string' ? describeValue(id, lookup) : m.value_unset();
}

/** Ranked classes as `[id, probability]` pairs, at most `count`. */
function ranked(value: unknown, lookup: Lookup | null, count = 3): SummaryItem[] {
	return list(value)
		.slice(0, count)
		.map((pair) => {
			const [id, probability] = list(pair);
			return { text: `${named(id, lookup)} ${percent(probability)}` };
		});
}

function row(label: string, items: SummaryItem[]): SummaryRow {
	return { label, items: items.length > 0 ? items : [{ text: m.signal_none() }] };
}

/** How well the OCR read each page it recognised; none where pages had text of their own. */
function readings(output: Json): SummaryRow[] {
	const pages = list(output.readings).map(object);
	if (pages.length === 0) return [];
	const items = pages.map((page) => {
		const words = typeof page.words === 'number' ? page.words : 0;
		const unsure = typeof page.unsure === 'number' ? page.unsure : 0;
		return {
			text: m.log_reading_page({
				page: String(page.page),
				unsure: percent(words > 0 ? unsure / words : 0),
				words: formatNumber(words)
			})
		};
	});
	return [{ label: m.log_reading(), items }];
}

/** The rows of a signal's entry, or of the OCR's; none for other nodes. */
export function signalSummary(entry: LogEntry, lookup: Lookup | null): SummaryRow[] {
	if (entry.node === 'ocr') return readings(object(entry.output));
	if (!(SIGNAL_NODES as readonly string[]).includes(entry.node)) return [];
	const output = object(entry.output);
	const abstained = text(output.abstained);
	if (abstained) return [{ label: m.signal_abstained(), items: [{ text: abstained }] }];
	switch (entry.node) {
		case 'signal_llm': {
			const proposal = object(output.proposal);
			const value = (key: string, inner = false) => {
				const found = text(inner ? object(proposal[key]).value : proposal[key]);
				return found ? [{ text: found }] : [];
			};
			return [
				row(m.signal_sender(), value('sender', true)),
				row(m.signal_recipient(), value('recipient')),
				row(m.field_document_type(), [
					...value('document_type'),
					...value('new_document_type').map((item) => ({
						text: `${item.text} (${m.candidate_new()})`
					}))
				]),
				...('topic' in proposal ? [row(m.field_topic(), value('topic'))] : []),
				row(m.field_date(), value('document_date', true)),
				row(m.field_title(), value('title')),
				row(m.signal_language(), value('language'))
			];
		}
		case 'signal_classifier':
			return [
				row(m.signal_contacts(), ranked(output.contacts, lookup)),
				row(m.signal_document_types(), ranked(output.document_types, lookup)),
				...('topics' in output ? [row(m.signal_topics(), ranked(output.topics, lookup))] : [])
			];
		case 'signal_neighbours':
			return [
				row(
					m.signal_neighbours(),
					list(output.neighbours).map((item, index) => {
						const found = object(item);
						const about = [found.contact_id, found.document_type_id, found.topic_id]
							.filter((id) => typeof id === 'string')
							.map((id) => named(id, lookup));
						const title = m.signal_document({ number: index + 1 });
						const said = about.length > 0 ? `${title}: ${about.join(', ')}` : title;
						const alike =
							typeof found.score === 'number'
								? m.signal_similarity({
										similarity: formatNumber(found.score, {
											minimumFractionDigits: 2,
											maximumFractionDigits: 2
										})
									})
								: null;
						return {
							text: alike ? `${said} · ${alike}` : said,
							href:
								typeof found.document_id === 'string' ? documentHref(found.document_id) : undefined
						};
					})
				)
			];
		case 'signal_facts':
			return [
				row(
					m.signal_named(),
					list(output.named).map((item) => ({ text: String(object(item).name ?? '') }))
				),
				row(
					m.signal_identifiers(),
					list(output.identifiers).map((item) => {
						const found = object(item);
						const topic =
							typeof found.topic_id === 'string' ? ` · ${named(found.topic_id, lookup)}` : '';
						return {
							text: `${kind(found.kind)} ${String(found.value ?? '')}: ${named(found.contact_id, lookup)}${topic}`
						};
					})
				),
				row(
					m.signal_unknown_identifiers(),
					list(output.unknown_identifiers).map((item) => {
						const found = object(item);
						return { text: `${kind(found.kind)} ${String(found.value ?? '')}` };
					})
				)
			];
		default:
			return [];
	}
}
