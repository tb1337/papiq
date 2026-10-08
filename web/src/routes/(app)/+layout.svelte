<script lang="ts">
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import AppShell from '#lib/components/AppShell.svelte';
	import { events } from '#lib/events.svelte.ts';
	import { inbox } from '#lib/inbox.svelte.ts';
	import { loginHref } from '#lib/navigation.ts';
	import { session } from '#lib/session.svelte.ts';

	let { children } = $props();

	// After a reconnect the counter may be stale.
	$effect(() => {
		void events.generation;
		if (session.user) void inbox.refresh();
	});

	// One event stream while someone is signed in; it closes with the session (sign-out, 401).
	$effect(() => {
		if (!session.user) return;
		let retry: ReturnType<typeof setTimeout> | undefined;
		const open = () =>
			events.start(async () => {
				try {
					if (!(await session.load())) {
						await goto(loginHref(page.url.pathname + page.url.search), { replaceState: true });
						return;
					}
				} catch {
					// The API is not reachable right now; try again below.
				}
				retry = setTimeout(open, 5000);
			});
		open();
		inbox.start();
		return () => {
			clearTimeout(retry);
			inbox.stop();
			events.stop();
		};
	});
</script>

<AppShell>
	{@render children()}
</AppShell>
