<script lang="ts">
	import Users from '@lucide/svelte/icons/users';
	import { m } from '#lib/paraglide/messages.js';
	import { canListAllUsers } from '#lib/permissions.ts';
	import { session } from '#lib/session.svelte.ts';
	import { cn } from '#lib/utils.ts';

	// Admins: the documents of all users instead of their own reach. Hidden for everyone else.
	let { pressed, onchange }: { pressed: boolean; onchange: (pressed: boolean) => void } = $props();
</script>

{#if canListAllUsers(session.user)}
	<button
		type="button"
		aria-pressed={pressed}
		title={m.all_users_hint()}
		onclick={() => onchange(!pressed)}
		class={cn(
			'inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-sm transition-colors',
			pressed
				? 'border-primary bg-secondary font-medium'
				: 'text-muted-foreground hover:text-foreground'
		)}
	>
		<Users class="size-3.5" aria-hidden="true" />
		{m.all_users()}
	</button>
{/if}
