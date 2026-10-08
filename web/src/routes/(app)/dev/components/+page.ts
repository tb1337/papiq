import { error } from '@sveltejs/kit';
import { dev } from '$app/env';

// A showcase of the building blocks, for development only.
export function load() {
	if (!dev) error(404);
}
