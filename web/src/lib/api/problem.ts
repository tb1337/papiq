import type { components } from './schema.ts';

export type Problem = components['schemas']['Problem'];

/** An answer of the API that is not a success: its status and, if sent, the problem details. */
export class ApiError extends Error {
	readonly status: number;
	readonly problem: Problem | null;
	/** Seconds from `Retry-After` (429), if the API sent it. */
	readonly retryAfter: number | null;

	constructor(status: number, problem: Problem | null, retryAfter: number | null) {
		super(problem?.detail ?? problem?.title ?? `HTTP ${status}`);
		this.name = 'ApiError';
		this.status = status;
		this.problem = problem;
		this.retryAfter = retryAfter;
	}
}

function isProblem(body: unknown): body is Problem {
	return !!body && typeof body === 'object' && 'status' in body && 'title' in body;
}

/** The problem details of a response (`application/problem+json`), or null. */
export async function readProblem(response: Response): Promise<Problem | null> {
	const type = response.headers.get('content-type') ?? '';
	if (!type.includes('json') || response.bodyUsed) return null;
	try {
		const body: unknown = await response.clone().json();
		return isProblem(body) ? body : null;
	} catch {
		return null; // not JSON after all
	}
}

/** Seconds from a `Retry-After` header (seconds or an HTTP date), or null. */
export function retryAfterSeconds(response: Response, now: number = Date.now()): number | null {
	const value = response.headers.get('retry-after');
	if (!value) return null;
	if (/^\d+$/.test(value.trim())) return Number(value.trim());
	const at = Date.parse(value);
	return Number.isNaN(at) ? null : Math.max(0, Math.ceil((at - now) / 1000));
}

/**
 * The error for a failed response. `body` is the error the API client already read from it
 * (`openapi-fetch` consumes the body); without it, the body is read here.
 */
export async function apiError(response: Response, body?: unknown): Promise<ApiError> {
	const problem = body === undefined ? await readProblem(response) : isProblem(body) ? body : null;
	return new ApiError(response.status, problem, retryAfterSeconds(response));
}
