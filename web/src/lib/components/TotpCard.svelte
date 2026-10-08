<script lang="ts">
	import { renderSVG } from 'uqr';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import CodeDialog from '#lib/components/CodeDialog.svelte';
	import SecretDialog from '#lib/components/SecretDialog.svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import * as Card from '#lib/components/ui/card/index.ts';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { reportError } from '#lib/errors.ts';
	import { m } from '#lib/paraglide/messages.js';

	type Setup = components['schemas']['TotpSetupOut'];

	// Second factor: set up with a QR code (the secret as text next to it), confirm with a code,
	// recovery codes shown once, new codes, turn off. The API checks every code.
	let { enabled, onchanged }: { enabled: boolean; onchanged: () => void } = $props();

	let setup = $state<Setup | null>(null);
	let confirming = $state(false);
	let disabling = $state(false);
	let renewing = $state(false);
	let codes = $state<string | null>(null);
	let busy = $state(false);

	const qr = $derived(setup ? renderSVG(setup.otpauth_uri, { border: 2 }) : '');

	async function start() {
		busy = true;
		try {
			setup = await unwrap(api.POST('/api/v1/auth/totp'));
		} catch (error) {
			reportError(error);
		} finally {
			busy = false;
		}
	}

	async function confirm(code: string) {
		const done = await unwrap(api.POST('/api/v1/auth/totp/confirm', { body: { code } }));
		setup = null;
		codes = done.recovery_codes.join('\n');
		onchanged();
	}

	async function disable(code: string) {
		await unwrap(api.POST('/api/v1/auth/totp/disable', { body: { code } }));
		toast.success(m.totp_disabled());
		onchanged();
	}

	async function renew(code: string) {
		const done = await unwrap(api.POST('/api/v1/auth/totp/recovery-codes', { body: { code } }));
		codes = done.recovery_codes.join('\n');
	}
</script>

<Card.Root>
	<Card.Header>
		<Card.Title class="flex items-center gap-2">
			{m.totp_title()}
			<Badge variant={enabled ? 'secondary' : 'outline'}>
				{enabled ? m.totp_on() : m.totp_off()}
			</Badge>
		</Card.Title>
		<Card.Description>{m.totp_hint()}</Card.Description>
	</Card.Header>
	<Card.Content class="flex flex-col gap-4">
		{#if setup}
			<div class="flex flex-wrap items-start gap-6">
				<div
					class="size-44 shrink-0 rounded-xl border bg-white p-1 [&>svg]:size-full"
					role="img"
					aria-label={m.totp_qr_label()}
				>
					<!-- eslint-disable-next-line svelte/no-at-html-tags -- SVG drawn by uqr from the setup URI -->
					{@html qr}
				</div>
				<div class="flex min-w-0 flex-1 flex-col gap-2">
					<p class="text-sm">{m.totp_scan()}</p>
					<p class="text-sm text-muted-foreground">{m.totp_manual()}</p>
					<code class="rounded-lg bg-secondary px-3 py-2 font-mono text-sm break-all select-all">
						{setup.secret}
					</code>
				</div>
			</div>
			<div class="flex gap-2">
				<Button onclick={() => (confirming = true)}>{m.totp_confirm()}</Button>
				<Button variant="ghost" onclick={() => (setup = null)}>{m.cancel()}</Button>
			</div>
		{:else if enabled}
			<div class="flex flex-wrap gap-2">
				<Button variant="outline" onclick={() => (renewing = true)}>{m.totp_new_codes()}</Button>
				<Button variant="outline" onclick={() => (disabling = true)}>{m.totp_disable()}</Button>
			</div>
		{:else}
			<div>
				<Button disabled={busy} onclick={start}>{m.totp_setup()}</Button>
			</div>
		{/if}
	</Card.Content>
</Card.Root>

<CodeDialog
	bind:open={confirming}
	title={m.totp_confirm()}
	description={m.totp_confirm_hint()}
	submitLabel={m.totp_turn_on()}
	onsubmit={confirm}
/>
<CodeDialog
	bind:open={disabling}
	title={m.totp_disable()}
	description={m.totp_disable_hint()}
	submitLabel={m.totp_disable()}
	destructive
	onsubmit={disable}
/>
<CodeDialog
	bind:open={renewing}
	title={m.totp_new_codes()}
	description={m.totp_new_codes_hint()}
	submitLabel={m.totp_new_codes()}
	onsubmit={renew}
/>
<SecretDialog
	secret={codes}
	title={m.totp_codes_title()}
	description={m.totp_codes_hint()}
	onclose={() => (codes = null)}
/>
