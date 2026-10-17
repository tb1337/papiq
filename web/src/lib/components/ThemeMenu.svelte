<script lang="ts">
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import { mode } from 'mode-watcher';
	import ThemeChoices from '#lib/components/ThemeChoices.svelte';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import { m } from '#lib/paraglide/messages.js';
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class={buttonVariants({ variant: 'secondary', size: 'icon' })}
		aria-label={m.theme_label()}
	>
		{#if mode.current === 'dark'}
			<Moon aria-hidden="true" />
		{:else}
			<Sun aria-hidden="true" />
		{/if}
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-48">
		<ThemeChoices />
	</DropdownMenu.Content>
</DropdownMenu.Root>
