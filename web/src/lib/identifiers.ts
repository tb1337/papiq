/** Identifiers of contacts as the contact dialog edits them. */
import type { components } from '#lib/api/schema.ts';
import { m } from '#lib/paraglide/messages.js';

export type IdentifierIn = components['schemas']['IdentifierIn'];
export type IdentifierOut = components['schemas']['IdentifierOut'];
export type IdentifierKind = IdentifierIn['kind'];
export type IdentifierSource = IdentifierOut['source'];

export const IDENTIFIER_KINDS: readonly IdentifierKind[] = [
	'iban',
	'vat_id',
	'customer_number',
	'personnel_number',
	'email_domain'
];

export function kindLabel(kind: IdentifierKind): string {
	switch (kind) {
		case 'iban':
			return m.identifier_kind_iban();
		case 'vat_id':
			return m.identifier_kind_vat_id();
		case 'customer_number':
			return m.identifier_kind_customer_number();
		case 'personnel_number':
			return m.identifier_kind_personnel_number();
		case 'email_domain':
			return m.identifier_kind_email_domain();
	}
}

/** One row of the editor; `key` keeps a row apart while its value is typed. `topic_id`: the
 * contact's topic it points to (empty: none). */
export interface IdentifierRow {
	key: number;
	kind: IdentifierKind;
	value: string;
	source: IdentifierSource;
	topic_id: string;
}

let next = 0;

export function row(
	kind: IdentifierKind = 'iban',
	value = '',
	source: IdentifierSource = 'manual',
	topic_id: string | null = null
): IdentifierRow {
	return { key: next++, kind, value, source, topic_id: topic_id ?? '' };
}

export function rowsOf(identifiers: readonly IdentifierOut[] | undefined): IdentifierRow[] {
	return (identifiers ?? []).map((item) => row(item.kind, item.value, item.source, item.topic_id));
}

/** What a change sends: the identifiers entered by hand (a learned one taken over among them),
 * each with its topic if it is one of `topics` (those the contact enables), and the rejected
 * ones; learned ones stay as they are. Empty rows are left out. */
export function changesOf(
	rows: readonly IdentifierRow[],
	topics?: readonly string[]
): {
	identifiers: IdentifierIn[];
	rejected_identifiers: IdentifierIn[];
} {
	const filled = rows.filter((item) => item.value.trim() !== '');
	const topicOf = (item: IdentifierRow) =>
		item.topic_id !== '' && (topics === undefined || topics.includes(item.topic_id))
			? { topic_id: item.topic_id }
			: {};
	return {
		identifiers: filled
			.filter((item) => item.source === 'manual')
			.map((item) => ({ kind: item.kind, value: item.value.trim(), ...topicOf(item) })),
		rejected_identifiers: filled
			.filter((item) => item.source === 'rejected')
			.map((item) => ({ kind: item.kind, value: item.value.trim() }))
	};
}
