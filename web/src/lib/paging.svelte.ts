/** A list that loads page by page ("load more"), with loading, empty and error states. */
export interface Page<T> {
	items: T[];
	/** The token for the next page; null on the last one. */
	next: string | number | null;
}

export class PagedList<T> {
	items = $state<T[]>([]);
	loading = $state(false);
	error = $state<unknown>(null);
	next = $state<string | number | null>(null);
	loaded = $state(false);
	#load: (after: string | number | null) => Promise<Page<T>>;
	// A reload while a page is in flight makes the older answer obsolete.
	#generation = 0;

	constructor(load: (after: string | number | null) => Promise<Page<T>>) {
		this.#load = load;
	}

	get hasMore(): boolean {
		return this.next !== null;
	}

	get empty(): boolean {
		return this.loaded && !this.loading && this.error === null && this.items.length === 0;
	}

	/** Start over from the first page. */
	async reload(): Promise<void> {
		this.items = [];
		this.next = null;
		this.loaded = false;
		await this.#fetch(null, true);
	}

	async more(): Promise<void> {
		if (this.next !== null && !this.loading) await this.#fetch(this.next, false);
	}

	/** Reload the pages shown so far without emptying the list (after an event). */
	async refresh(): Promise<void> {
		const count = this.items.length;
		const generation = ++this.#generation;
		// A page in flight is obsolete now and will not clear `loading` itself.
		this.loading = false;
		let items: T[] = [];
		let next: string | number | null = null;
		try {
			do {
				const page = await this.#load(next);
				items = items.concat(page.items);
				next = page.next;
			} while (next !== null && items.length < count);
		} catch (error) {
			// A list already on screen stays; the next event or reload tries again.
			if (generation === this.#generation && !this.loaded) this.error = error;
			return;
		}
		if (generation !== this.#generation) return;
		this.items = items;
		this.next = next;
		this.error = null;
		this.loaded = true;
	}

	async #fetch(after: string | number | null, replace: boolean): Promise<void> {
		const generation = ++this.#generation;
		this.loading = true;
		this.error = null;
		try {
			const page = await this.#load(after);
			if (generation !== this.#generation) return;
			this.items = replace ? page.items : this.items.concat(page.items);
			this.next = page.next;
			this.loaded = true;
		} catch (error) {
			if (generation === this.#generation) this.error = error;
		} finally {
			if (generation === this.#generation) this.loading = false;
		}
	}
}
