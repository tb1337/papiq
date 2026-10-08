import { goto } from '$app/navigation';
import { loginHref } from '#lib/navigation.ts';
import { connectSession } from '#lib/session.svelte.ts';

// A single-page app: no server rendering, no prerendering; the API delivers all data.
export const ssr = false;
export const prerender = false;

// An expired session anywhere leads to the sign-in page, returning here afterwards.
connectSession(() => {
	const here = window.location.pathname + window.location.search;
	void goto(loginHref(here), { replaceState: true });
});
