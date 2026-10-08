<script lang="ts">
	import '../app.css';
	import { ModeWatcher } from 'mode-watcher';
	import { Toaster } from '#lib/components/ui/sonner/index.ts';
	import { getLocale } from '#lib/i18n.ts';

	let { children } = $props();

	$effect(() => {
		document.documentElement.lang = getLocale();
	});
</script>

<!-- No transition-blocking <style> element: the CSP allows no inline style elements. -->
<ModeWatcher disableHeadScriptInjection disableTransitions={false} />
<Toaster richColors={false} closeButton />
{@render children()}
