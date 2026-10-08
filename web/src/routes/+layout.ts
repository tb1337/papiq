import { goto } from '$app/navigation';
import { home, loginHref } from '#lib/navigation.ts';
import { connectSession } from '#lib/session.svelte.ts';

// A single-page app: no server rendering, no prerendering; the API delivers all data.
export const ssr = false;
export const prerender = false;

connectSession({
	// An expired session anywhere leads to the sign-in page, returning here afterwards.
	unauthorized: () => {
		const here = window.location.pathname + window.location.search;
		void goto(loginHref(here), { replaceState: true });
	},
	// Someone else signed in in another tab: reload, so nothing of the previous user stays.
	replaced: () => window.location.assign(home())
});
