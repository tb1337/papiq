/**
 * Applying a rule to existing documents: what the preview selects and what the request holds
 * (decision E3: documents with a conflict are only applied with their own consent, which
 * becomes `accept_conflicts`), and how often the progress is asked for.
 */
import type { components } from '#lib/api/schema.ts';

export type PreviewItem = components['schemas']['ApplyPreviewItemOut'];
export type Application = components['schemas']['RuleApplicationOut'];
export type ApplyRequest = components['schemas']['ApplyRequest'];

export function hasConflict(item: PreviewItem): boolean {
	return item.conflicts.length > 0;
}

/** The documents chosen when they show up: those without a conflict. */
export function preselected(items: readonly PreviewItem[]): string[] {
	return items.filter((item) => !hasConflict(item)).map((item) => item.document_id);
}

/** The request for the chosen documents, in the order of the preview. A conflict counts only
 * as accepted; a chosen document with a conflict that is not accepted stays out. */
export function applyRequest(
	version: number,
	items: readonly PreviewItem[],
	chosen: ReadonlySet<string>,
	accepted: ReadonlySet<string>
): ApplyRequest {
	const included = items.filter((item) =>
		hasConflict(item) ? accepted.has(item.document_id) : chosen.has(item.document_id)
	);
	return {
		version,
		document_ids: included.map((item) => item.document_id),
		accept_conflicts: included.filter(hasConflict).map((item) => item.document_id)
	};
}

export function isFinished(application: Pick<Application, 'status'>): boolean {
	return application.status === 'done' || application.status === 'failed';
}

/** Every second at first, then every three. */
export function pollDelay(elapsedMs: number): number {
	return elapsedMs < 10_000 ? 1000 : 3000;
}

/**
 * Ask for the progress until the application is finished or `signal` aborts; `update` gets each
 * answer. Resolves with the last one.
 */
export async function follow(
	first: Application,
	load: (id: string) => Promise<Application>,
	update: (application: Application) => void,
	signal: AbortSignal,
	wait: (ms: number) => Promise<void> = (ms) => new Promise((resolve) => setTimeout(resolve, ms))
): Promise<Application> {
	let current = first;
	let elapsed = 0;
	while (!isFinished(current) && !signal.aborted) {
		const delay = pollDelay(elapsed);
		await wait(delay);
		elapsed += delay;
		if (signal.aborted) break;
		current = await load(current.id);
		update(current);
	}
	return current;
}
