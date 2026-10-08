import { describe, expect, it } from 'vitest';
import { oidcErrorMessage } from './oidc-errors.ts';

describe('oidcErrorMessage', () => {
	it('has a text for every code of the API', () => {
		for (const code of ['denied', 'failed', 'conflict', 'provider']) {
			expect(oidcErrorMessage(code)).toBeTruthy();
		}
	});

	it('ignores anything else', () => {
		expect(oidcErrorMessage(null)).toBeNull();
		expect(oidcErrorMessage('<script>')).toBeNull();
	});
});
