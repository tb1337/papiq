import { describe, expect, it } from 'vitest';
import { regionalLocale } from './i18n.ts';

describe('regionalLocale', () => {
	it("takes the browser's region for the UI language", () => {
		expect(regionalLocale('en', ['de-DE', 'en-GB', 'en'])).toBe('en-GB');
		expect(regionalLocale('de', ['de-AT', 'en-US'])).toBe('de-AT');
		expect(regionalLocale('de', ['DE-ch'])).toBe('DE-ch');
	});

	it('falls back to Germany and the US', () => {
		expect(regionalLocale('de', ['en-GB', 'de'])).toBe('de-DE');
		expect(regionalLocale('en', [])).toBe('en-US');
	});

	it('does not take a language that merely starts alike', () => {
		expect(regionalLocale('de', ['dev-XX'])).toBe('de-DE');
	});
});
