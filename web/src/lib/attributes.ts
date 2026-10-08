/** Attribute values between the API's form and the text a form field holds. */
import type { components } from '#lib/api/schema.ts';

export type ApiValue = components['schemas']['DocumentDetails']['attributes'][string];
export type AttributeType = components['schemas']['AttributeType'];
/** Text for most types; `true`/`false`/'' for yes/no; amount and currency for money. */
export type FieldValue = string | { amount: string; currency: string };

export const DEFAULT_CURRENCY = 'EUR';

export function emptyValue(type: AttributeType): FieldValue {
	return type === 'amount' ? { amount: '', currency: DEFAULT_CURRENCY } : '';
}

export function toField(type: AttributeType, value: ApiValue | undefined): FieldValue {
	if (value === undefined || value === null) return emptyValue(type);
	if (type === 'amount' && typeof value === 'object') return { ...value };
	if (type === 'boolean') return value === true ? 'true' : value === false ? 'false' : '';
	return String(value);
}

/** The value for the API, or null when the field is empty (which removes the value). */
export function toApi(type: AttributeType, value: FieldValue): ApiValue | null {
	if (type === 'amount') {
		const money = typeof value === 'object' ? value : { amount: '', currency: DEFAULT_CURRENCY };
		const amount = money.amount.trim().replace(',', '.');
		return amount === '' ? null : { amount, currency: money.currency.trim().toUpperCase() };
	}
	const text = typeof value === 'string' ? value.trim() : '';
	if (text === '') return null;
	if (type === 'boolean') return text === 'true';
	if (type === 'number') return text.replace(',', '.');
	return text;
}

/** Equal values, so that an untouched attribute does not end up in a change. */
export function sameValue(a: ApiValue | null | undefined, b: ApiValue | null | undefined): boolean {
	return JSON.stringify(a ?? null) === JSON.stringify(b ?? null);
}
