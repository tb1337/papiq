import { describe, expect, it } from 'vitest';
import { apiError, readProblem, retryAfterSeconds } from './problem.ts';

describe('readProblem', () => {
	it('reads problem details', async () => {
		const response = new Response(
			JSON.stringify({ type: 'about:blank', title: 'Not Found', status: 404, detail: 'No.' }),
			{ status: 404, headers: { 'content-type': 'application/problem+json' } }
		);
		expect(await readProblem(response)).toMatchObject({ status: 404, detail: 'No.' });
		// The body stays readable.
		expect(response.bodyUsed).toBe(false);
	});

	it('ignores other bodies', async () => {
		expect(await readProblem(new Response('oops', { status: 502 }))).toBeNull();
		const json = new Response('{"x":1}', { headers: { 'content-type': 'application/json' } });
		expect(await readProblem(json)).toBeNull();
		const broken = new Response('{', { headers: { 'content-type': 'application/json' } });
		expect(await readProblem(broken)).toBeNull();
	});
});

describe('retryAfterSeconds', () => {
	it('reads seconds', () => {
		const response = new Response(null, { status: 429, headers: { 'retry-after': '30' } });
		expect(retryAfterSeconds(response)).toBe(30);
	});

	it('reads an HTTP date', () => {
		const now = Date.parse('2026-10-08T12:00:00Z');
		const response = new Response(null, {
			status: 429,
			headers: { 'retry-after': 'Thu, 08 Oct 2026 12:01:30 GMT' }
		});
		expect(retryAfterSeconds(response, now)).toBe(90);
	});

	it('is null without a usable header', () => {
		expect(retryAfterSeconds(new Response(null, { status: 429 }))).toBeNull();
		const garbage = new Response(null, { status: 429, headers: { 'retry-after': 'soon' } });
		expect(retryAfterSeconds(garbage)).toBeNull();
	});
});

describe('apiError', () => {
	it('carries status, problem and the wait', async () => {
		const response = new Response(
			JSON.stringify({ type: 'about:blank', title: 'Too Many Requests', status: 429 }),
			{
				status: 429,
				headers: { 'content-type': 'application/problem+json', 'retry-after': '5' }
			}
		);
		const error = await apiError(response);
		expect(error.status).toBe(429);
		expect(error.retryAfter).toBe(5);
		expect(error.message).toBe('Too Many Requests');
	});
});
