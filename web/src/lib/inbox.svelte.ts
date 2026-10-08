/** How many documents wait in the inbox (the counter in the navigation). */
import { api } from '#lib/api/client.ts';
import { events } from '#lib/events.svelte.ts';

const LIMIT = 100;

class InboxCount {
	count = $state(0);
	/** More than `LIMIT`: shown as "100+". */
	more = $state(false);
	#timer: ReturnType<typeof setTimeout> | undefined;
	#stop: (() => void) | null = null;

	async refresh(): Promise<void> {
		const { data } = await api.GET('/api/v1/inbox', { params: { query: { limit: LIMIT } } });
		if (!data) return;
		this.count = data.items.length;
		this.more = data.next_cursor !== null;
	}

	/** Follow the event stream while someone is signed in. */
	start(): void {
		if (this.#stop) return;
		void this.refresh();
		const stopEvents = events.subscribe((event) => {
			if (event.type === 'document.received' || event.type === 'document.step_completed') return;
			clearTimeout(this.#timer);
			this.#timer = setTimeout(() => void this.refresh(), 500);
		});
		this.#stop = () => {
			clearTimeout(this.#timer);
			stopEvents();
		};
	}

	stop(): void {
		this.#stop?.();
		this.#stop = null;
		this.count = 0;
		this.more = false;
	}
}

export const inbox = new InboxCount();
