import { render, screen } from '@testing-library/svelte';
import { tick } from 'svelte';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { m } from '#lib/paraglide/messages.js';
import ProcessingLog from './ProcessingLog.svelte';

const fetchMock = vi.fn<(request: Request) => Promise<Response>>();

function entry(step: string) {
	return {
		step,
		run: 1,
		outcome: 'ok',
		reason: null,
		confidence: null,
		model_version: 'fake 1',
		input: {},
		output: {},
		pipeline_version: 'test',
		started_at: '2026-10-09T10:00:00Z',
		duration_ms: 12
	};
}

function answer(entries: unknown[]): Response {
	return new Response(JSON.stringify(entries), {
		status: 200,
		headers: { 'content-type': 'application/json' }
	});
}

afterEach(() => vi.unstubAllGlobals());

describe('ProcessingLog', () => {
	it('ignores the answer of a document it has left behind', async () => {
		let releaseFirst: (() => void) | undefined;
		fetchMock.mockImplementation(async (request) => {
			if (request.url.includes('/documents/first/')) {
				await new Promise<void>((resolve) => (releaseFirst = resolve));
				return answer([entry('ocr')]);
			}
			return answer([entry('classify')]);
		});
		vi.stubGlobal('fetch', fetchMock);
		const { rerender } = render(ProcessingLog, { documentId: 'first' });
		await tick();
		await rerender({ documentId: 'second' });
		await screen.findByText(m.step_classify());
		releaseFirst?.();
		await tick();
		await new Promise((resolve) => setTimeout(resolve, 0));
		expect(screen.queryByText(m.step_ocr())).toBeNull();
		expect(screen.getByText(m.step_classify())).toBeTruthy();
	});
});
