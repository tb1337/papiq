/**
 * The Lucide icons of the build, each its own file loaded when it is shown: the names come from
 * `import.meta.glob` over the per-icon modules of `@lucide/svelte`, so every icon is in the
 * build (no CDN, the CSP allows `'self'` only) and none is loaded before it is needed. The map
 * of all 1,866 is large (about 50 KB gzip): only the icon picker imports it, and `load.ts` for
 * an icon beyond those offered first (`curated-modules.ts`), both when they need it.
 */
import type { Component } from 'svelte';

const MODULES = import.meta.glob<{ default: Component }>('./*.svelte', {
	base: '/node_modules/@lucide/svelte/dist/icons'
});

const PATHS = new Map(
	Object.keys(MODULES).map((path) => [path.slice('./'.length, -'.svelte'.length), path])
);

/** Every icon's name, sorted. */
export const ICON_NAMES: readonly string[] = [...PATHS.keys()].sort();

export function isIcon(name: string): boolean {
	return PATHS.has(name);
}

const loaded = new Map<string, Promise<Component | null>>();

/** The icon named `name`, loaded once; null for a name Lucide does not have. */
export function loadIcon(name: string): Promise<Component | null> {
	let found = loaded.get(name);
	if (!found) {
		const path = PATHS.get(name);
		found = path
			? MODULES[path]().then(
					(module) => module.default,
					() => null
				)
			: Promise.resolve(null);
		loaded.set(name, found);
	}
	return found;
}
