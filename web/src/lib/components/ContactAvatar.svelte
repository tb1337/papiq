<script lang="ts">
	import { initials } from '#lib/contacts.ts';
	import { cn } from '#lib/utils.ts';

	// A contact's logo, round on white (logos are drawn for light ground), or its initials when it
	// has none or the logo cannot be loaded. Decorative: the contact's name is next to it.
	let {
		contact,
		size = 'sm',
		class: className
	}: {
		contact: { name: string; logo?: { url: string } | null };
		size?: 'xs' | 'sm' | 'md' | 'lg' | 'xl';
		class?: string;
	} = $props();

	const SIZES = {
		xs: 'size-5 text-[0.5625rem]',
		sm: 'size-6 text-[0.625rem]',
		md: 'size-8 text-xs',
		lg: 'size-12 text-base',
		xl: 'size-14 text-lg'
	};
	let broken = $state<string | null>(null);
	const url = $derived(contact.logo?.url ?? null);
</script>

{#if url && broken !== url}
	<span
		class={cn(
			'inline-grid shrink-0 overflow-hidden rounded-full border bg-white',
			SIZES[size],
			className
		)}
		aria-hidden="true"
	>
		<img
			src={url}
			alt=""
			loading="lazy"
			decoding="async"
			class="size-full object-contain p-[12%]"
			onerror={() => (broken = url)}
		/>
	</span>
{:else}
	<span
		class={cn(
			'inline-grid shrink-0 place-items-center rounded-full bg-panel font-semibold tracking-wide text-brand',
			SIZES[size],
			className
		)}
		aria-hidden="true"
		data-initials
	>
		{initials(contact.name)}
	</span>
{/if}
