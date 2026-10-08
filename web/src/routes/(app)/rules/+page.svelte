<script lang="ts">
	import Plus from '@lucide/svelte/icons/plus';
	import Trash2 from '@lucide/svelte/icons/trash-2';
	import { onMount } from 'svelte';
	import { toast } from 'svelte-sonner';
	import { api } from '#lib/api/client.ts';
	import { unwrap } from '#lib/api/call.ts';
	import type { components } from '#lib/api/schema.ts';
	import { BASE } from '#lib/base.ts';
	import AllUsersToggle from '#lib/components/AllUsersToggle.svelte';
	import ConfirmDialog from '#lib/components/ConfirmDialog.svelte';
	import { Badge } from '#lib/components/ui/badge/index.ts';
	import { Button, buttonVariants } from '#lib/components/ui/button/index.ts';
	import { Switch } from '#lib/components/ui/switch/index.ts';
	import { describeError, reportError } from '#lib/errors.ts';
	import type { UserName } from '#lib/masterdata.svelte.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { canChangeRule } from '#lib/permissions.ts';
	import { TRIGGER_LABELS } from '#lib/rules/describe.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	type Rule = components['schemas']['RuleOut'];

	let rules = $state<Rule[] | null>(null);
	let users = $state<UserName[]>([]);
	let problem = $state<string | null>(null);
	let allUsers = $state(false);
	let removing = $state<Rule | null>(null);
	let busy = $state<string | null>(null);

	const ownerName = (id: string) => users.find((user) => user.id === id)?.username ?? id;

	async function load() {
		try {
			[rules, users] = await Promise.all([
				unwrap(
					api.GET('/api/v1/rules', {
						params: { query: { include_disabled: true, all_users: allUsers || undefined } }
					})
				),
				users.length > 0 ? users : unwrap(api.GET('/api/v1/users'))
			]);
			problem = null;
		} catch (error) {
			problem = describeError(error);
		}
	}

	onMount(() => void load());

	async function switchRule(rule: Rule, enabled: boolean) {
		busy = rule.id;
		try {
			const changed = await unwrap(
				api.PATCH('/api/v1/rules/{id}', { params: { path: { id: rule.id } }, body: { enabled } })
			);
			rules = (rules ?? []).map((entry) => (entry.id === changed.id ? changed : entry));
			toast.success(enabled ? m.rule_switched_on() : m.rule_switched_off());
		} catch (error) {
			reportError(error);
		} finally {
			busy = null;
		}
	}

	async function remove() {
		if (!removing) return;
		await unwrap(api.DELETE('/api/v1/rules/{id}', { params: { path: { id: removing.id } } }));
		toast.success(m.rule_deleted());
		await load();
	}
</script>

<svelte:head><title>{m.nav_rules()} · p:api:q</title></svelte:head>

<div class="flex flex-col gap-6">
	<div class="flex flex-wrap items-center justify-between gap-3">
		<h1 class="text-[1.75rem] font-semibold tracking-tight">{m.nav_rules()}</h1>
		<a href="{BASE}/rules/new" class={cn(buttonVariants())}>
			<Plus aria-hidden="true" />
			{m.rules_new()}
		</a>
	</div>

	<div class="flex flex-wrap gap-2">
		<AllUsersToggle
			pressed={allUsers}
			onchange={(pressed) => {
				allUsers = pressed;
				void load();
			}}
		/>
	</div>

	{#if problem}
		<div class="flex flex-col items-start gap-3" role="alert">
			<p class="text-destructive">{problem}</p>
			<Button variant="outline" onclick={load}>{m.retry()}</Button>
		</div>
	{:else if !rules}
		<p class="text-muted-foreground">{m.loading()}</p>
	{:else if rules.length === 0}
		<p
			class="rounded-2xl border border-dashed bg-card px-6 py-16 text-center text-muted-foreground"
		>
			{m.rules_none()}
		</p>
	{:else}
		<ul class="flex flex-col gap-2">
			{#each rules as rule (rule.id)}
				<li class="flex flex-wrap items-center gap-3 rounded-2xl border bg-card p-4">
					<div class="flex min-w-0 flex-1 flex-col gap-1">
						<span class="flex flex-wrap items-center gap-2">
							<a
								href="{BASE}/rules/{rule.id}"
								class="truncate font-medium hover:underline"
								aria-label={m.rule_open({ name: rule.name })}>{rule.name}</a
							>
							<Badge variant={rule.scope === 'global' ? 'secondary' : 'outline'}>
								{rule.scope === 'global' ? m.rule_global() : m.rule_personal()}
							</Badge>
							{#if rule.owner_id && rule.owner_id !== session.user?.id}
								<Badge variant="outline">{ownerName(rule.owner_id)}</Badge>
							{/if}
							{#if !rule.enabled}<Badge variant="outline">{m.rule_off()}</Badge>{/if}
						</span>
						<span class="text-sm text-muted-foreground">
							{m.rule_priority_short({ priority: rule.priority })} ·
							{rule.triggers.map((trigger) => TRIGGER_LABELS[trigger]()).join(' · ')}
						</span>
						{#if rule.disabled_reason}
							<span class="text-sm text-destructive">
								{m.rule_disabled_reason({ reason: rule.disabled_reason })}
							</span>
						{/if}
					</div>
					{#if canChangeRule(rule, session.user)}
						<div class="flex items-center gap-2">
							<Switch
								checked={rule.enabled}
								disabled={busy === rule.id}
								aria-label={rule.enabled
									? m.rule_switch_off({ name: rule.name })
									: m.rule_switch_on({ name: rule.name })}
								onCheckedChange={(enabled) => switchRule(rule, enabled)}
							/>
							<Button
								variant="ghost"
								size="icon"
								aria-label={m.rule_delete({ name: rule.name })}
								onclick={() => (removing = rule)}
							>
								<Trash2 aria-hidden="true" />
							</Button>
						</div>
					{:else}
						<Badge variant={rule.enabled ? 'secondary' : 'outline'}>
							{rule.enabled ? m.rule_on() : m.rule_off()}
						</Badge>
					{/if}
				</li>
			{/each}
		</ul>
	{/if}
</div>

<ConfirmDialog
	bind:open={() => removing !== null, (open) => !open && (removing = null)}
	title={m.rule_delete({ name: removing?.name ?? '' })}
	description={m.rule_delete_confirm()}
	confirmLabel={m.delete_title()}
	onconfirm={remove}
/>
