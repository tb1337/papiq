<script lang="ts">
	import Languages from '@lucide/svelte/icons/languages';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import { changeLanguage, getLocale, LANGUAGE_NAMES, locales, type Locale } from '#lib/i18n.ts';
	import { m } from '#lib/paraglide/messages.js';

	const current = getLocale();
</script>

<DropdownMenu.Root>
	<DropdownMenu.Trigger
		class={buttonVariants({ variant: 'secondary', size: 'default' })}
		aria-label={`${m.language_label()}: ${LANGUAGE_NAMES[current]}`}
	>
		<Languages aria-hidden="true" />
		<span class="uppercase">{current}</span>
	</DropdownMenu.Trigger>
	<DropdownMenu.Content align="end" class="w-44">
		<DropdownMenu.Group>
			<DropdownMenu.GroupHeading>{m.language_label()}</DropdownMenu.GroupHeading>
			<DropdownMenu.RadioGroup
				value={current}
				onValueChange={(value) => changeLanguage(value as Locale)}
			>
				{#each locales as locale (locale)}
					<DropdownMenu.RadioItem value={locale} lang={locale}>
						{LANGUAGE_NAMES[locale]}
					</DropdownMenu.RadioItem>
				{/each}
			</DropdownMenu.RadioGroup>
		</DropdownMenu.Group>
	</DropdownMenu.Content>
</DropdownMenu.Root>
