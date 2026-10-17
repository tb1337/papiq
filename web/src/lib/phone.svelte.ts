/**
 * Whether the window is as narrow as a phone: below Tailwind's `sm` (40rem). Master data take
 * their phone layout there: rows that open the editor as a whole, editors on the full screen,
 * pickers as bottom sheets. Where markup only differs in looks, CSS (`max-sm:`, `sm:`) decides;
 * this is for markup that differs in structure. Tests choose with `override`.
 */
import { MediaQuery } from 'svelte/reactivity';

export const PHONE_QUERY = '(width < 40rem)';

class Phone {
	#query: MediaQuery | null = null;
	#override = $state<boolean | null>(null);

	get current(): boolean {
		if (this.#override !== null) return this.#override;
		this.#query ??= new MediaQuery(PHONE_QUERY);
		return this.#query.current;
	}

	/** For tests: a phone (true), a wider screen (false), or the window again (null). */
	override(value: boolean | null): void {
		this.#override = value;
	}
}

export const phone = new Phone();
