import { describe, expect, it, vi } from 'vitest';
import { events } from './events.svelte.ts';

class FakeSource {
	static last: FakeSource;
	onopen: (() => void) | null = null;
	onerror: (() => void) | null = null;
	readyState = 1;
	closed = false;
	listeners = new Map<string, (message: { data: string }) => void>();
	constructor(readonly url: string) {
		FakeSource.last = this;
	}
	addEventListener(type: string, handler: (message: { data: string }) => void) {
		this.listeners.set(type, handler);
	}
	close() {
		this.closed = true;
		this.readyState = 2;
	}
}

events.useFactory((url) => new FakeSource(url) as unknown as EventSource);

describe('event stream', () => {
	it('passes events on and reloads after a break', () => {
		const seen: string[] = [];
		const stop = events.subscribe((event) => seen.push(event.type));
		events.start(() => {});
		const source = FakeSource.last;
		expect(source.url).toBe('/api/v1/events');
		source.onopen?.();
		source.listeners.get('document.filed')?.({
			data: JSON.stringify({ type: 'document.filed', id: 'e1', occurred_at: '', document_id: 'd1' })
		});
		expect(seen).toEqual(['document.filed']);

		const before = events.generation;
		source.onerror?.();
		source.onopen?.();
		expect(events.generation).toBe(before + 1);

		source.listeners.get('document.updated')?.({ data: 'not json' });
		expect(seen).toHaveLength(1);
		stop();
		events.stop();
		expect(source.closed).toBe(true);
	});

	it('reports a stream the browser gave up on', () => {
		const closed = vi.fn();
		events.start(closed);
		const source = FakeSource.last;
		source.readyState = 2;
		source.onerror?.();
		expect(closed).toHaveBeenCalledOnce();
		expect(source.closed).toBe(true);
	});

	it('reloads after a stream the server closed was started again', () => {
		events.start(() => {});
		FakeSource.last.onopen?.();
		const before = events.generation;
		FakeSource.last.readyState = 2;
		FakeSource.last.onerror?.(); // the API restarted: 502 from the proxy, the browser gave up
		events.start(() => {});
		FakeSource.last.onopen?.();
		expect(events.generation).toBe(before + 1);
		events.stop();
	});
});
