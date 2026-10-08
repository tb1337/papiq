<script lang="ts">
	import type { Snippet } from 'svelte';
	import LogOut from '@lucide/svelte/icons/log-out';
	import Menu from '@lucide/svelte/icons/menu';
	import { goto } from '$app/navigation';
	import { page } from '$app/state';
	import LanguageMenu from '#lib/components/LanguageMenu.svelte';
	import Logo from '#lib/components/Logo.svelte';
	import ThemeMenu from '#lib/components/ThemeMenu.svelte';
	import { buttonVariants } from '#lib/components/ui/button/index.ts';
	import * as DropdownMenu from '#lib/components/ui/dropdown-menu/index.ts';
	import * as Sheet from '#lib/components/ui/sheet/index.ts';
	import { reportError } from '#lib/errors.ts';
	import { home, loginHref } from '#lib/navigation.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { href, isCurrent, SETTINGS, visibleSections, type Section } from '#lib/sections.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	let { children }: { children: Snippet } = $props();

	const sections = $derived(visibleSections(session.isAdmin));
	const userSections = $derived(sections.filter((section) => !section.admin));
	const adminSections = $derived(sections.filter((section) => section.admin));
	const username = $derived(session.user?.username ?? '');
	let menuOpen = $state(false);

	async function signOut() {
		try {
			await session.logout();
		} catch (error) {
			reportError(error);
			return;
		}
		await goto(loginHref(home()), { replaceState: true });
	}
</script>

{#snippet pill(section: Section, vertical: boolean)}
	{@const current = isCurrent(section, page.url.pathname)}
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
	</a>
{/snippet}

<a
	href="#main"
	class="sr-only z-50 rounded-xl bg-primary px-4 py-2 text-primary-foreground focus:not-sr-only focus:fixed focus:top-3 focus:left-3"
	>{m.skip_to_content()}</a
>

<div class="flex min-h-screen flex-col">
	<header class="sticky top-0 z-40 border-b bg-card">
		<div class="flex h-16 items-center gap-4 px-4 sm:px-8 lg:gap-7">
			<Sheet.Root bind:open={menuOpen}>
				<Sheet.Trigger
					class={cn(buttonVariants({ variant: 'ghost', size: 'icon' }), 'lg:hidden')}
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

			<nav aria-label={m.nav_main()} class="hidden min-w-0 flex-1 items-center gap-1 lg:flex">
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

			<div class="ml-auto flex items-center gap-2">
				<LanguageMenu />
				<ThemeMenu />
				<DropdownMenu.Root>
					<DropdownMenu.Trigger
						class={cn(
							buttonVariants({ size: 'icon' }),
							'font-semibold uppercase [&]:text-[0.95rem]'
						)}
						aria-label={m.account_menu({ username })}
					>
						{username.slice(0, 1)}
					</DropdownMenu.Trigger>
					<DropdownMenu.Content align="end" class="w-56">
						<DropdownMenu.Label class="flex flex-col gap-0.5">
							<span class="font-semibold">{username}</span>
							<span class="text-xs font-normal text-muted-foreground">
								{session.isAdmin ? m.role_admin() : m.role_user()}
							</span>
						</DropdownMenu.Label>
						<DropdownMenu.Separator />
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

	<main id="main" tabindex="-1" class="flex flex-1 justify-center px-4 py-8 outline-none sm:px-8">
		<div class="w-full max-w-[1040px]">
			{@render children()}
		</div>
	</main>
</div>
