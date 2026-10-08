import { redirect } from '@sveltejs/kit';
import { loginHref } from '#lib/navigation.ts';
import { session } from '#lib/session.svelte.ts';

// Every page here needs a signed-in user; without one, sign in and come back.
export async function load({ url }) {
	if (!(await session.load())) redirect(307, loginHref(url.pathname + url.search));
}
