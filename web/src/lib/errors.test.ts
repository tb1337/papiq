import { describe, expect, it } from 'vitest';
import { ApiError, type Problem } from '#lib/api/problem.ts';
import { m } from '#lib/paraglide/messages.js';
import { describeError, fieldErrors } from './errors.ts';

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

describe('fieldErrors', () => {
	it('splits a 422 by field', () => {
		const error = new ApiError(
			422,
			problem(422, 'title: String should have at least 1 character; attributes.abc-1: bad value'),
			null
		);
		expect(fieldErrors(error)).toEqual({
			title: 'String should have at least 1 character',
			'attribute:abc-1': 'bad value'
		});
	});

	it('is empty for other errors', () => {
		expect(fieldErrors(new ApiError(409, problem(409, 'title: x'), null))).toEqual({});
		expect(fieldErrors(new Error('x'))).toEqual({});
		expect(fieldErrors(new ApiError(422, problem(422, 'Name is taken.'), null))).toEqual({});
	});
});
