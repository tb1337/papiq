import { toast } from 'svelte-sonner';
import { ApiError } from '#lib/api/problem.ts';
import { m } from '#lib/paraglide/messages.js';

/** A text for the user about a failed call. Problem details stay as the API wrote them. */
export function describeError(error: unknown): string {
	if (error instanceof ApiError) {
		if (error.status === 429) {
			return error.retryAfter !== null
				? m.error_too_many({ seconds: error.retryAfter })
				: m.error_too_many_later();
		}
		const detail = error.problem?.detail ?? null;
		if (error.status === 403)
			return detail ? `${m.error_forbidden()} ${detail}` : m.error_forbidden();
		return detail ?? error.problem?.title ?? m.error_unexpected({ status: error.status });
	}
	// fetch rejects with a TypeError when the server cannot be reached.
	if (error instanceof TypeError) return m.error_network();
	return m.error_unexpected({ status: 0 });
}

/** Show a failed call as a toast. */
export function reportError(error: unknown): void {
	toast.error(describeError(error));
}

/**
 * The messages of a 422 by field. The API writes the details as `field: message; field: message`
 * (`fields.<id>` for a field); anything else stays out of the result.
 */
export function fieldErrors(error: unknown): Record<string, string> {
	const found: Record<string, string> = {};
	if (!(error instanceof ApiError) || error.status !== 422) return found;
	for (const part of (error.problem?.detail ?? '').split('; ')) {
		const match = /^([\w-]+(?:\.[\w-]+)*): (.+)$/.exec(part);
		if (!match) continue;
		const name = match[1].replace(/^fields\./, 'field:');
		found[name] = match[2];
	}
	return found;
}
