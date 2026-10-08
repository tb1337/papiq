/** Texts for values in a change: ids become names, the rest is shown plainly. */
import { formatDate, formatMoney } from '#lib/i18n.ts';
import type { Lookup } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';

const FIELD_LABELS: Record<string, () => string> = {
	title: m.field_title,
	contact: m.field_contact,
	document_type: m.field_document_type,
	tags: m.field_tags,
	document_date: m.field_date,
	drawer: m.field_drawer,
	correspondent: m.field_contact
};

/** The name of a field in a change: `attribute:<id>` shows the attribute's name. */
export function fieldLabel(field: string, lookup: Lookup | null): string {
	if (field.startsWith('attribute:')) {
		const id = field.slice('attribute:'.length);
		return lookup?.attributes.find((attribute) => attribute.id === id)?.name ?? field;
	}
	return FIELD_LABELS[field]?.() ?? field;
}

/** A value as the user reads it. */
export function describeValue(value: unknown, lookup: Lookup | null): string {
	if (value === null || value === undefined || value === '') return m.value_unset();
	if (Array.isArray(value)) return value.map((entry) => describeValue(entry, lookup)).join(', ');
	if (typeof value === 'boolean') return value ? m.value_yes() : m.value_no();
	if (typeof value === 'object') {
		const money = value as { amount?: unknown; currency?: unknown };
		if (typeof money.amount === 'string' && typeof money.currency === 'string') {
			return formatMoney(money.amount, money.currency);
		}
		return JSON.stringify(value);
	}
	const text = String(value);
	if (lookup) {
		const named = [
			...lookup.contacts,
			...lookup.documentTypes,
			...lookup.tags,
			...lookup.drawers
		].find((entry) => entry.id === text);
		if (named) return named.name;
		const user = lookup.users.find((entry) => entry.id === text);
		if (user) return user.username;
	}
	return /^\d{4}-\d{2}-\d{2}$/.test(text) ? formatDate(text) : text;
}
