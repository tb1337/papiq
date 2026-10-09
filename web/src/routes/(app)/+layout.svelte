<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import AppShell from '#lib/components/AppShell.svelte';
	import { events } from '#lib/events.svelte.ts';
	import { inbox } from '#lib/inbox.svelte.ts';
	import { loginHref } from '#lib/navigation.ts';
	import { session } from '#lib/session.svelte.ts';
	import { uploads } from '#lib/upload.svelte.ts';

	let { children } = $props();

	// The effects below follow the signed-in user, not the user object: a reload of the session
	// (every navigation) must not restart the stream or drop the uploads.
	const userId = $derived(session.user?.id ?? null);

	// After a reconnect the counter may be stale.
	$effect(() => {
		void events.generation;
		if (userId) {
			void inbox.refresh();
			uploads.recheck();
		}
	});

	// One event stream while someone is signed in; it closes with the session (sign-out, 401).
	$effect(() => {
		if (!userId) return;
		let retry: ReturnType<typeof setTimeout> | undefined;
		let ended = false;
		const open = () =>
			events.start(async () => {
				if (ended) return;
				try {
					if (!(await session.load())) {
						await goto(loginHref(page.url.pathname + page.url.search), { replaceState: true });
						return;
					}
				} catch {
					// The API is not reachable right now; try again below.
				}
				if (!ended) retry = setTimeout(open, 5000);
			});
		open();
		inbox.start();
		return () => {
			ended = true;
			clearTimeout(retry);
			inbox.stop();
			events.stop();
			uploads.reset();
		};
	});
</script>

<AppShell>
	{@render children()}
</AppShell>
