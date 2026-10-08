<script lang="ts">
	import KeyRound from '@lucide/svelte/icons/key-round';
	import OctagonX from '@lucide/svelte/icons/octagon-x';
	import { tick } from 'svelte';
	import { goto } from '$app/navigation';
	import { api } from '#lib/api/client.ts';
	import { apiError } from '#lib/api/problem.ts';
	import LanguageMenu from '#lib/components/LanguageMenu.svelte';
	import Logo from '#lib/components/Logo.svelte';
	import ThemeMenu from '#lib/components/ThemeMenu.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Field from '#lib/components/ui/field/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { describeError } from '#lib/errors.ts';
	import { oidcLoginHref } from '#lib/navigation.ts';
	import { oidcErrorMessage } from '#lib/oidc-errors.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { session } from '#lib/session.svelte.ts';

	let { data } = $props();

	type Step = 'password' | 'totp' | 'recovery';
	let step = $state<Step>('password');
	let username = $state('');
	let password = $state('');
	let code = $state('');
	// An abandoned or refused sign-in at the identity provider comes back with a code.
	let message = $derived<string | null>(oidcErrorMessage(data.error));
	let busy = $state(false);
	let codeInput = $state<HTMLInputElement | null>(null);

	// The code field takes the focus as soon as it appears.
	async function showStep(next: Step) {
		step = next;
		code = '';
		await tick();
		codeInput?.focus();
	}

	async function submit(event: SubmitEvent) {
		event.preventDefault();
		if (busy) return;
		busy = true;
		message = null;
		try {
			const {
				data: signedIn,
				error,
				response
			} = await api.POST('/api/v1/auth/login', {
				body: {
					username,
					password,
					code: step === 'totp' ? code.replace(/\s/g, '') : null,
					recovery_code: step === 'recovery' ? code.trim() : null
				}
			});
			if (signedIn) {
				session.start(signedIn);
				password = '';
				await goto(data.next, { replaceState: true });
			} else if (response.status === 401 && error?.second_factor_required) {
				await showStep('totp');
			} else if (response.status === 401) {
				message = step === 'password' ? m.login_failed() : m.login_code_failed();
			} else {
				message = describeError(await apiError(response, error));
			}
		} catch (error) {
			message = describeError(error);
		} finally {
			busy = false;
		}
	}

	function back() {
		step = 'password';
		code = '';
		message = null;
	}

	function swapCode() {
		message = null;
		void showStep(step === 'recovery' ? 'totp' : 'recovery');
	}
</script>

<svelte:head><title>{m.login_submit()} · p:api:q</title></svelte:head>

<div class="flex min-h-screen">
	<aside class="hidden w-[560px] shrink-0 flex-col justify-between bg-panel p-14 lg:flex">
		<Logo class="text-xl" />
		<div class="flex flex-col gap-5">
			<Logo class="text-[88px] leading-none tracking-[-0.04em]" />
			<p class="max-w-[380px] text-xl leading-normal text-muted-foreground">{m.login_tagline()}</p>
		</div>
		<div></div>
	</aside>

	<div class="flex flex-1 flex-col">
		<div class="flex items-center justify-between gap-2 px-6 py-6 sm:px-8 lg:justify-end">
			<Logo class="text-xl lg:hidden" />
			<div class="flex items-center gap-2">
				<LanguageMenu />
				<ThemeMenu />
			</div>
		</div>

		<main class="flex flex-1 items-center justify-center px-6 pb-20">
			<div class="flex w-full max-w-[400px] flex-col gap-7">
				<div class="flex flex-col gap-2">
					<h1 class="text-[1.875rem] font-semibold tracking-tight">
						{step === 'password' ? m.login_title() : m.login_second_title()}
					</h1>
					<p class="text-muted-foreground">
						{#if step === 'password'}{m.login_subtitle()}{:else if step === 'totp'}{m.login_totp_hint()}{:else}{m.login_recovery_hint()}{/if}
					</p>
				</div>

				<form class="flex flex-col gap-5" onsubmit={submit} novalidate>
					{#if step === 'password'}
						<Field.Field>
							<Field.Label for="username">{m.login_username()}</Field.Label>
							<Input
								id="username"
								name="username"
								autocomplete="username"
								required
								bind:value={username}
								aria-invalid={message ? true : undefined}
								aria-describedby={message ? 'login-error' : undefined}
								class="h-12"
							/>
						</Field.Field>
						<Field.Field>
							<Field.Label for="password">{m.login_password()}</Field.Label>
							<Input
								id="password"
								name="password"
								type="password"
								autocomplete="current-password"
								required
								bind:value={password}
								aria-invalid={message ? true : undefined}
								aria-describedby={message ? 'login-error' : undefined}
								class="h-12"
							/>
						</Field.Field>
					{:else if step === 'totp'}
						<Field.Field>
							<Field.Label for="code">{m.login_totp_label()}</Field.Label>
							<Input
								id="code"
								name="code"
								inputmode="numeric"
								autocomplete="one-time-code"
								maxlength={8}
								required
								bind:value={code}
								aria-invalid={message ? true : undefined}
								aria-describedby={message ? 'login-error' : undefined}
								bind:ref={codeInput}
								class="h-14 text-center font-mono text-2xl tracking-[0.4em]"
							/>
						</Field.Field>
					{:else}
						<Field.Field>
							<Field.Label for="recovery">{m.login_recovery_label()}</Field.Label>
							<Input
								id="recovery"
								name="recovery"
								autocomplete="off"
								spellcheck={false}
								required
								bind:value={code}
								aria-invalid={message ? true : undefined}
								aria-describedby={message ? 'login-error' : undefined}
								bind:ref={codeInput}
								class="h-14 text-center font-mono text-lg"
							/>
						</Field.Field>
					{/if}

					{#if message}
						<div
							id="login-error"
							role="alert"
							class="flex items-start gap-2.5 rounded-xl bg-lane-red px-4 py-3.5 text-sm text-lane-red-foreground"
						>
							<OctagonX class="mt-px size-[18px] shrink-0" aria-hidden="true" />
							<span>{message}</span>
						</div>
					{/if}

					<!-- Not disabled while busy: the focus stays on the button. -->
					<Button type="submit" size="lg" aria-disabled={busy}>{m.login_submit()}</Button>

					{#if step === 'password'}
						{#if data.oidc}
							<Button variant="outline" size="lg" href={oidcLoginHref(data.next)}>
								<KeyRound aria-hidden="true" />
								{m.login_oidc({ provider: data.oidc })}
							</Button>
						{/if}
					{:else}
						<div class="flex justify-between text-sm">
							<button
								type="button"
								class="text-muted-foreground hover:text-foreground"
								onclick={back}>{m.login_back()}</button
							>
							<button type="button" class="font-medium text-brand" onclick={swapCode}>
								{step === 'recovery' ? m.login_use_totp() : m.login_use_recovery()}
							</button>
						</div>
					{/if}
				</form>
			</div>
		</main>
	</div>
</div>
