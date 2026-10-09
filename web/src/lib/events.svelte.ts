/**
 * One `EventSource` on `/api/v1/events` for the signed-in session. Events are thin; pages listen
 * for the types they care about and fetch the state afresh. There is no replay: after a
 * reconnect `generation` grows and pages reload what they show. The stream ends on sign-out.
 */
import { BASE_API } from '#lib/api/base.ts';

export const EVENT_TYPES = [
	'document.received',
	'document.step_completed',
	'document.lane_changed',
	'document.filed',
	'document.updated',
	'document.deleted'
] as const;
export type EventType = (typeof EVENT_TYPES)[number];

export interface DocumentEvent {
	type: EventType;
	id: string;
	occurred_at: string;
	document_id: string;
	[key: string]: unknown;
}

type Handler = (event: DocumentEvent) => void;

class EventStream {
	/** Grows with every reconnect after a break: shown data may be stale. */
	generation = $state(0);
	connected = $state(false);
	#source: EventSource | null = null;
	#handlers = new Set<Handler>();
	// Whether a stream was open before: every later `onopen` is a reconnect (after a network
	// break, or after the server closed the stream and the caller started it again).
	#opened = false;
	#factory: (url: string) => EventSource = (url) => new EventSource(url);

	/** For tests. */
	useFactory(factory: (url: string) => EventSource): void {
		this.#factory = factory;
	}

	/** Listen to all document events; returns the function that stops listening. */
	subscribe(handler: Handler): () => void {
		this.#handlers.add(handler);
		return () => this.#handlers.delete(handler);
	}

	/**
	 * Open the stream. `onClosed` is called when the browser gave up on it (the API answered with
	 * an error, e.g. 401 after the session ended); the caller decides whether to start again.
	 */
	start(onClosed: () => void): void {
		if (this.#source) return;
		const source = this.#factory(`${BASE_API}/events`);
		this.#source = source;
		source.onopen = () => {
			this.connected = true;
			if (this.#opened) this.generation++;
			this.#opened = true;
		};
		source.onerror = () => {
			this.connected = false;
			// Network breaks are retried by the browser itself; a closed source is not.
			if (source.readyState === 2) {
				this.stop();
				onClosed();
			}
		};
		for (const type of EVENT_TYPES) {
			source.addEventListener(type, (message) => {
				try {
					const event = JSON.parse((message as MessageEvent<string>).data) as DocumentEvent;
					for (const handler of this.#handlers) handler(event);
				} catch {
					// A malformed event is ignored; the next reconnect reloads anyway.
				}
			});
		}
	}

	stop(): void {
		this.#source?.close();
		this.#source = null;
		this.connected = false;
	}
}

export const events = new EventStream();
