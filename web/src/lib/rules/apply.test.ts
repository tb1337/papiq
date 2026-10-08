import { describe, expect, it } from 'vitest';
import {
	applyRequest,
	follow,
	pollDelay,
	preselected,
	type Application,
	type PreviewItem
} from './apply.ts';

const item = (id: string, conflict = false): PreviewItem => ({
	document_id: id,
	title: id,
	changes: [{ field: 'tags', old: [], new: ['t'] }],
	conflicts: conflict
		? [{ field: 'contact', kind: 'conflict', reason: 'has another contact' }]
		: [],
	notes: []
});

const application = (status: Application['status'], done: number): Application => ({
	id: 'a1',
	rule_id: 'r1',
	version: 2,
	status,
	total: 3,
	done,
	applied: done,
	unchanged: 0,
	skipped: [],
	error: null,
	created_at: '2026-10-08T10:00:00Z',
	finished_at: null
});

describe('applying a rule', () => {
	const items = [item('a'), item('b', true), item('c'), item('d', true)];

	it('chooses documents without a conflict', () => {
		expect(preselected(items)).toEqual(['a', 'c']);
	});

	it('applies conflicts only with their own consent', () => {
		const request = applyRequest(2, items, new Set(['a', 'b', 'c']), new Set(['d']));
		expect(request).toEqual({ version: 2, document_ids: ['a', 'c', 'd'], accept_conflicts: ['d'] });
		expect(applyRequest(2, items, new Set(), new Set()).document_ids).toEqual([]);
	});

	it('asks every second, later every three', () => {
		expect([0, 9000, 10_000, 60_000].map(pollDelay)).toEqual([1000, 1000, 3000, 3000]);
	});

	it('follows the progress until it is done', async () => {
		const answers = [application('running', 1), application('running', 2), application('done', 3)];
		const waits: number[] = [];
		const seen: number[] = [];
		const last = await follow(
			application('queued', 0),
			async () => answers.shift()!,
			(current) => seen.push(current.done),
			new AbortController().signal,
			async (ms) => void waits.push(ms)
		);
		expect([last.status, seen, waits]).toEqual(['done', [1, 2, 3], [1000, 1000, 1000]]);
	});

	it('stops following when the page goes', async () => {
		const stop = new AbortController();
		const last = await follow(
			application('running', 1),
			async () => application('running', 2),
			() => stop.abort(),
			stop.signal,
			async () => undefined
		);
		expect(last.done).toBe(2);
	});
});
