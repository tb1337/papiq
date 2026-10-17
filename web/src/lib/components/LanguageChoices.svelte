<script lang="ts">
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import { changeLanguage, getLocale, LANGUAGE_NAMES, locales, type Locale } from '#lib/i18n.ts';
	import { m } from '#lib/paraglide/messages.js';

	// The languages as a group of a menu: the language menu's own, and the account menu's on a
	// phone (where the header has no room for the language menu).
	const current = getLocale();
</script>

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
