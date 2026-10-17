<script lang="ts">
	import CircleDashed from '@lucide/svelte/icons/circle-dashed';
	import type { Component } from 'svelte';
	import { loadIcon } from '#lib/icons/load.ts';
	import { cn } from '#lib/utils.ts';

	// A Lucide icon by its name, from the own build and loaded when it is shown (`lazy`: once it
	// scrolls into view; one beyond those offered first loads the index of all icons first,
	// `load.ts`); a name Lucide does not have shows a stand-in. Decorative: the name of what it
	// stands for is next to it.
	let {
		name,
		class: className = 'size-4',
		lazy = false
	}: { name: string; class?: string; lazy?: boolean } = $props();

	let icon = $state<Component | null | undefined>(undefined);
	let visible = $state(false);
	let placeholder = $state<HTMLSpanElement | null>(null);

	$effect(() => {
		if (!lazy || typeof IntersectionObserver === 'undefined') {
			visible = true;
			return;
		}
		const target = placeholder;
		if (!target || visible) return;
		const observer = new IntersectionObserver((entries) => {
			if (entries.some((entry) => entry.isIntersecting)) {
				visible = true;
				observer.disconnect();
			}
		});
		observer.observe(target);
		return () => observer.disconnect();
	});

	$effect(() => {
		if (!visible) return;
		const wanted = name;
		let current = true;
		loadIcon(wanted).then((found) => {
			if (current) icon = found;
		});
		return () => {
			current = false;
		};
	});
</script>

{#if icon}
	{@const Icon = icon}
	<Icon class={cn('shrink-0', className)} aria-hidden="true" />
{:else if icon === null}
	<CircleDashed class={cn('shrink-0', className)} aria-hidden="true" data-unknown-icon />
{:else}
	<span bind:this={placeholder} class={cn('inline-block shrink-0', className)} aria-hidden="true"
	></span>
{/if}
