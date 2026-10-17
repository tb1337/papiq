/**
 * Loading an icon by its name: one offered first from the small map (`curated-modules.ts`), any
 * other from the map of every Lucide icon (`lucide.ts`), which is then loaded once. Pages with
 * chips and type icons so carry the small map only.
 */
import type { Component } from 'svelte';
import { curatedModule } from '#lib/icons/curated-modules.ts';

const loaded = new Map<string, Promise<Component | null>>();

/** The icon named `name`, loaded once; null for a name Lucide does not have. */
export function loadIcon(name: string): Promise<Component | null> {
	let found = loaded.get(name);
	if (!found) {
		const curated = curatedModule(name);
		found = curated
			? curated().then(
					(module) => module.default,
					() => null
				)
			: import('#lib/icons/lucide.ts').then(
					(index) => index.loadIcon(name),
					() => null
				);
		loaded.set(name, found);
	}
	return found;
}
