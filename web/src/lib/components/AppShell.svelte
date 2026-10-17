<script lang="ts">
	import type { Snippet } from 'svelte';
	import LogOut from '@lucide/svelte/icons/log-out';
	import Menu from '@lucide/svelte/icons/menu';
	import SearchIcon from '@lucide/svelte/icons/search';
	import Users from '@lucide/svelte/icons/users';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import { BASE } from '#lib/base.ts';
	import HeaderSearch from '#lib/components/HeaderSearch.svelte';
	import LanguageChoices from '#lib/components/LanguageChoices.svelte';
	import LanguageMenu from '#lib/components/LanguageMenu.svelte';
	import Logo from '#lib/components/Logo.svelte';
	import ThemeChoices from '#lib/components/ThemeChoices.svelte';
	import ThemeMenu from '#lib/components/ThemeMenu.svelte';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import * as Sheet from '#lib/components/ui/sheet/index.ts';
	import { reportError } from '#lib/errors.ts';
	import { home, loginHref } from '#lib/navigation.ts';
	import { inbox } from '#lib/inbox.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { canListAllUsers } from '#lib/permissions.ts';
	import { phone } from '#lib/phone.svelte.ts';
	import { scope } from '#lib/scope.svelte.ts';
	import { href, isCurrent, SETTINGS, visibleSections, type Section } from '#lib/sections.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	let { children }: { children: Snippet } = $props();

	const sections = $derived(visibleSections(session.isAdmin));
	const userSections = $derived(sections.filter((section) => !section.admin));
	const adminSections = $derived(sections.filter((section) => section.admin));
	const username = $derived(session.user?.username ?? '');
	let menuOpen = $state(false);
	// A document opened from the inbox belongs to the inbox.
	const currentPath = $derived(
		page.url.searchParams.get('from') === 'inbox' ? `${home()}inbox` : page.url.pathname
	);

	async function signOut() {
		try {
			await session.logout();
		} catch (error) {
			reportError(error);
			return;
		}
		await goto(loginHref(home()), { replaceState: true });
	}

	// The inbox counter covers what the scope setting covers.
	let allBefore = scope.allUsers;
	$effect(() => {
		const all = scope.allUsers;
		if (all === allBefore) return;
		allBefore = all;
		void inbox.refresh();
	});
</script>

{#snippet pill(section: Section, vertical: boolean)}
	{@const current = isCurrent(section, currentPath)}
	<a
		href={href(section)}
		aria-current={current ? 'page' : undefined}
		onclick={() => (menuOpen = false)}
		class={cn(
			'flex h-9 items-center gap-2 rounded-full px-3.5 text-sm whitespace-nowrap transition-colors hover:text-foreground',
			current ? 'bg-secondary font-medium text-foreground' : 'text-muted-foreground',
			vertical && 'h-11 rounded-xl px-4 text-base'
		)}
	>
		{#if vertical}<section.icon class="size-[18px]" aria-hidden="true" />{/if}
		{section.label()}
		{#if section.path === '/inbox' && inbox.count > 0}
			<span
				class="grid h-5 min-w-5 place-items-center rounded-full bg-primary px-1.5 text-xs font-semibold text-primary-foreground"
				aria-label={m.inbox_count({ count: inbox.count })}
			>
				{inbox.more ? `${inbox.count}+` : inbox.count}
			</span>
		{/if}
	</a>
{/snippet}

<a
	href="#main"
	class="sr-only z-50 rounded-xl bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
	>{m.skip_to_content()}</a
>

<!-- A page that needs the room (the document view) marks itself `data-wide`: from the desktop
	width on it fills the window, and only its own areas scroll. -->
<div class="flex min-h-screen flex-col lg:has-[[data-wide]]:h-dvh">
	<header class="sticky top-0 z-40 border-b bg-card">
		<!-- Narrower gaps on a phone, and a little narrower below 380 pixels, so that the menu, the
		     wordmark and the buttons fit beside each other down to 360. -->
		<div class="flex h-16 items-center gap-2 px-4 max-[380px]:px-3 sm:gap-4 sm:px-8 lg:gap-7">
			<Sheet.Root bind:open={menuOpen}>
				<Sheet.Trigger
					class={cn(
						buttonVariants({ variant: 'ghost', size: 'icon' }),
						adminSections.length ? 'xl:hidden' : 'lg:hidden'
					)}
					aria-label={m.nav_open()}
				>
					<Menu aria-hidden="true" />
				</Sheet.Trigger>
				<Sheet.Content side="left" class="w-72 p-4">
					<Sheet.Header class="px-2">
						<Sheet.Title><Logo class="text-lg" /></Sheet.Title>
					</Sheet.Header>
					<nav aria-label={m.nav_main()} class="flex flex-col gap-1">
						{#each userSections as section (section.path)}
							{@render pill(section, true)}
						{/each}
						{#if adminSections.length}
							<div class="mx-4 my-2 h-px bg-border"></div>
							{#each adminSections as section (section.path)}
								{@render pill(section, true)}
							{/each}
						{/if}
					</nav>
				</Sheet.Content>
			</Sheet.Root>

			<a href={home()} class="rounded-md text-lg"><Logo /></a>

			<!-- The menu in the header where it fits: from lg, from xl with the admin's entries. -->
			<nav
				aria-label={m.nav_main()}
				class={cn(
					'hidden min-w-0 flex-1 items-center gap-1',
					adminSections.length ? 'xl:flex' : 'lg:flex'
				)}
			>
				{#each userSections as section (section.path)}
					{@render pill(section, false)}
				{/each}
				{#if adminSections.length}
					<div class="mx-2 h-5 w-px bg-border" aria-hidden="true"></div>
					{#each adminSections as section (section.path)}
						{@render pill(section, false)}
					{/each}
				{/if}
			</nav>

			<div class="ml-auto flex items-center gap-1.5 max-[380px]:gap-1 sm:gap-2">
				<!-- The field where there is room: instead of the menu from md, beside it from xl
				     (the admin's menu from xl, the field beside it from 2xl). -->
				<HeaderSearch
					class={cn(
						'hidden w-64 md:flex',
						adminSections.length ? 'xl:hidden 2xl:flex 2xl:w-80' : 'lg:hidden xl:flex xl:w-80'
					)}
				/>
				<a
					href="{BASE}/documents#search"
					class={cn(
						buttonVariants({ variant: 'ghost', size: 'icon' }),
						'md:hidden',
						adminSections.length ? 'xl:inline-flex 2xl:hidden' : 'lg:inline-flex xl:hidden'
					)}
					aria-label={m.header_search_label()}
				>
					<SearchIcon aria-hidden="true" />
				</a>
				<!-- On a phone, language and theme are in the account menu: the header has no room. -->
				{#if !phone.current}
					<LanguageMenu />
					<ThemeMenu />
				{/if}
				<DropdownMenu.Root>
					<DropdownMenu.Trigger
						class={cn(
							buttonVariants({ size: 'icon' }),
							'relative font-semibold uppercase [&]:text-[0.95rem]'
						)}
						aria-label={scope.allUsers
							? m.account_menu_all_users({ username })
							: m.account_menu({ username })}
					>
						{username.slice(0, 1)}
						<!-- An admin who sees all users is told so at a glance. -->
						{#if scope.allUsers}
							<span
								class="absolute -top-0.5 -right-0.5 grid size-4 place-items-center rounded-full border-2 border-card bg-lane-yellow text-lane-yellow-foreground"
								aria-hidden="true"
							>
								<Users class="size-2.5" />
							</span>
						{/if}
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="end" class="w-64">
						<DropdownMenu.Label class="flex flex-col gap-0.5">
							<span class="font-semibold">{username}</span>
							<span class="text-xs font-normal text-muted-foreground">
								{session.isAdmin ? m.role_admin() : m.role_user()}
							</span>
						</DropdownMenu.Label>
						<DropdownMenu.Separator />
						{#if canListAllUsers(session.user)}
							<DropdownMenu.CheckboxItem
								checked={scope.allUsers}
								onCheckedChange={(checked) => scope.set(checked)}
								class="items-start"
							>
								<Users aria-hidden="true" class="mt-0.5" />
								<span class="flex flex-col gap-0.5">
									{m.all_users()}
									<span class="text-xs text-muted-foreground">{m.all_users_hint()}</span>
								</span>
							</DropdownMenu.CheckboxItem>
							<DropdownMenu.Separator />
						{/if}
						{#if phone.current}
							<LanguageChoices />
							<DropdownMenu.Separator />
							<ThemeChoices />
							<DropdownMenu.Separator />
						{/if}
						<DropdownMenu.Item onSelect={() => goto(href(SETTINGS))}>
							<SETTINGS.icon aria-hidden="true" />
							{SETTINGS.label()}
						</DropdownMenu.Item>
						<DropdownMenu.Item onSelect={signOut}>
							<LogOut aria-hidden="true" />
							{m.sign_out()}
						</DropdownMenu.Item>
					</DropdownMenu.Content>
				</DropdownMenu.Root>
			</div>
		</div>
	</header>

	<main
		id="main"
		tabindex="-1"
		class="flex flex-1 justify-center px-4 py-8 outline-none sm:px-8 lg:has-[[data-wide]]:min-h-0 lg:has-[[data-wide]]:py-6"
	>
		<div
			class="w-full max-w-[1040px] has-[[data-wide]]:max-w-[1600px] lg:has-[[data-wide]]:min-h-0"
		>
			{@render children()}
		</div>
	</main>
</div>
