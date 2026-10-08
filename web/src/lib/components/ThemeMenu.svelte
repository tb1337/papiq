<script lang="ts">
	import Monitor from '@lucide/svelte/icons/monitor';
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import { mode, setMode, userPrefersMode } from 'mode-watcher';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Follows the system until chosen; mode-watcher keeps the choice in this browser.
	const choices = [
		{ value: 'light', label: m.theme_light, icon: Sun },
		{ value: 'dark', label: m.theme_dark, icon: Moon },
		{ value: 'system', label: m.theme_system, icon: Monitor }
	] as const;
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
		<DropdownMenu.Group>
			<DropdownMenu.GroupHeading>{m.theme_label()}</DropdownMenu.GroupHeading>
			<DropdownMenu.RadioGroup
				value={userPrefersMode.current}
				onValueChange={(value) => setMode(value as 'light' | 'dark' | 'system')}
			>
				{#each choices as choice (choice.value)}
					<DropdownMenu.RadioItem value={choice.value}>
						<choice.icon aria-hidden="true" />
						{choice.label()}
					</DropdownMenu.RadioItem>
				{/each}
			</DropdownMenu.RadioGroup>
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
