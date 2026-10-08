/**
 * Language of the UI and formats of dates and numbers. Paraglide picks the language: the choice
 * stored in this browser, else the browser's language, else English. Texts live in
 * `messages/{de,en}.json`; texts of the API (problem details) stay as the API sends them.
 */
import { getLocale, locales, setLocale, type Locale } from '#lib/paraglide/runtime.js';

export { getLocale, locales, type Locale };

/** Each language in its own words, for the language switch. */
export const LANGUAGE_NAMES: Record<Locale, string> = { de: 'Deutsch', en: 'English' };

const DEFAULT_REGION: Record<Locale, string> = { de: 'de-DE', en: 'en-US' };

/** Switch the language; the page reloads in it and the choice stays in this browser. */
export function changeLanguage(locale: Locale): void {
	if (locale !== getLocale()) setLocale(locale);
}

/**
 * The locale for formats: the browser's own regional variant of the UI language if it has one
 * (`en-GB` for English in Britain), else German as in Germany or English as in the US.
 */
export function regionalLocale(
	locale: Locale,
	preferred: readonly string[] = typeof navigator === 'undefined' ? [] : navigator.languages
): string {
	const own = preferred.find((tag) => tag.toLowerCase().startsWith(`${locale}-`));
	return own ?? DEFAULT_REGION[locale];
}

function formatLocale(): string {
	return regionalLocale(getLocale());
}

/** A date (and optionally time) in the user's format. */
export function formatDate(
	value: Date | string,
	options: Intl.DateTimeFormatOptions = { dateStyle: 'medium' }
): string {
	const date = typeof value === 'string' ? new Date(value) : value;
	return new Intl.DateTimeFormat(formatLocale(), options).format(date);
}

/** A number in the user's format. */
export function formatNumber(value: number, options?: Intl.NumberFormatOptions): string {
	return new Intl.NumberFormat(formatLocale(), options).format(value);
}

/** An amount of money; `amount` is the API's exact decimal text, `currency` an ISO 4217 code. */
export function formatMoney(amount: string, currency: string): string {
	return new Intl.NumberFormat(formatLocale(), { style: 'currency', currency }).format(
		Number(amount)
	);
}
