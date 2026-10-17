<script lang="ts">
	import Monitor from '@lucide/svelte/icons/monitor';
	import Moon from '@lucide/svelte/icons/moon';
	import Sun from '@lucide/svelte/icons/sun';
	import { setMode, userPrefersMode } from 'mode-watcher';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import { m } from '#lib/paraglide/messages.js';

	// Light, dark or as the system, as a group of a menu: the theme menu's own, and the account
	// menu's on a phone. Follows the system until chosen; mode-watcher keeps the choice in this
	// browser.
	const choices = [
		{ value: 'light', label: m.theme_light, icon: Sun },
		{ value: 'dark', label: m.theme_dark, icon: Moon },
		{ value: 'system', label: m.theme_system, icon: Monitor }
	] as const;
</script>

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
