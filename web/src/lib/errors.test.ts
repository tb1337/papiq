import { describe, expect, it } from 'vitest';
import { ApiError, type Problem } from '#lib/api/problem.ts';
import { m } from '#lib/paraglide/messages.js';
import { describeError } from './errors.ts';

const problem = (status: number, detail: string | null = null): Problem => ({
	type: 'about:blank',
	title: 'Title',
	status,
	detail,
	existing_document_id: null,
	open_fields: null,
	second_factor_required: null
});

describe('describeError', () => {
	it('keeps the detail of the API', () => {
		expect(describeError(new ApiError(409, problem(409, 'Name taken.'), null))).toBe('Name taken.');
		expect(describeError(new ApiError(409, problem(409), null))).toBe('Title');
	});

	it('tells how long to wait', () => {
		expect(describeError(new ApiError(429, problem(429), 12))).toBe(
			m.error_too_many({ seconds: 12 })
		);
		expect(describeError(new ApiError(429, null, null))).toBe(m.error_too_many_later());
	});

	it('names a missing right', () => {
		expect(describeError(new ApiError(403, problem(403, 'Admins only.'), null))).toBe(
			`${m.error_forbidden()} Admins only.`
		);
	});

	it('names an unreachable server', () => {
		expect(describeError(new TypeError('Failed to fetch'))).toBe(m.error_network());
	});

	it('falls back to the status', () => {
		expect(describeError(new ApiError(502, null, null))).toBe(m.error_unexpected({ status: 502 }));
	});
});
