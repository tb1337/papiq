<script lang="ts">
	import Check from '@lucide/svelte/icons/check';
	import { untrack } from 'svelte';
	import { Button } from '#lib/components/ui/button/index.ts';
	import { Input } from '#lib/components/ui/input/index.ts';
	import { isHex, isPalette, PALETTE, type PaletteColor } from '#lib/colors.ts';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	// A colour of the palette, an own one or none: twelve swatches, and behind "More colours" the
	// browser's colour picker with a field for `#rrggbb`.
	let {
		value = $bindable(null),
		id
	}: {
		value?: string | null;
		id: string;
	} = $props();

	const NAMES: Record<PaletteColor, () => string> = {
		gray: m.color_gray,
		red: m.color_red,
		orange: m.color_orange,
		amber: m.color_amber,
		lime: m.color_lime,
		green: m.color_green,
		teal: m.color_teal,
		sky: m.color_sky,
		blue: m.color_blue,
		violet: m.color_violet,
		fuchsia: m.color_fuchsia,
		pink: m.color_pink
	};
	const FALLBACK = '#b45309';

	let more = $state(false);
	let text = $state('');
	// The own colour the field shows: it follows one chosen elsewhere (the dialog opening with
	// one, the browser's picker), never what is being typed.
	let shown = '';
	const own = $derived(isHex(value));
	const shownMore = $derived(more || own);

	$effect(() => {
		const current = value;
		if (current && isHex(current) && current !== shown) {
			shown = current;
			untrack(() => (text = current));
		}
	});

	function typed(next: string) {
		text = next;
		const candidate = next.trim().startsWith('#') ? next.trim() : `#${next.trim()}`;
		if (isHex(candidate)) {
			value = candidate.toLowerCase();
			shown = value;
		}
	}

	function picked(next: string) {
		value = next.toLowerCase();
	}

	// The swatches as a radio group: the arrows move the choice, Tab leaves the group. On a phone
	// each takes a tap within 44 pixels (36 and 4 beyond the border, the gaps 8: none overlap).
	const CHOICES: readonly (PaletteColor | null)[] = [null, ...PALETTE];
	const focused = $derived<PaletteColor | null>(isPalette(value) ? value : null);

	function choose(color: PaletteColor | null) {
		value = color;
		more = false;
	}

	function arrows(event: KeyboardEvent & { currentTarget: HTMLElement }) {
		const step = { ArrowRight: 1, ArrowDown: 1, ArrowLeft: -1, ArrowUp: -1 }[event.key];
		if (!step) return;
		event.preventDefault();
		const next = CHOICES[(CHOICES.indexOf(focused) + step + CHOICES.length) % CHOICES.length];
		choose(next);
		const radios = event.currentTarget.querySelectorAll<HTMLElement>('[role="radio"]');
		radios[CHOICES.indexOf(next)]?.focus();
	}

	function openMore() {
		more = true;
		if (!own) {
			text = FALLBACK;
			value = FALLBACK;
		}
	}
</script>

<div class="flex flex-col gap-3">
	<div
		role="radiogroup"
		aria-labelledby="{id}-label"
		class="flex flex-wrap gap-1.5 max-sm:gap-2"
		tabindex="-1"
		onkeydown={arrows}
	>
		<button
			type="button"
			role="radio"
			aria-checked={value === null}
			tabindex={focused === null ? 0 : -1}
			aria-label={m.color_none()}
			title={m.color_none()}
			class="relative grid size-8 place-items-center rounded-full border border-dashed bg-background aria-checked:ring-2 aria-checked:ring-ring aria-checked:ring-offset-2 aria-checked:ring-offset-background max-sm:size-9 max-sm:after:absolute max-sm:after:-inset-[5px]"
			onclick={() => choose(null)}
		>
			{#if value === null}<Check class="size-4 text-muted-foreground" aria-hidden="true" />{/if}
		</button>
		{#each PALETTE as color (color)}
			<button
				type="button"
				role="radio"
				aria-checked={value === color}
				tabindex={focused === color ? 0 : -1}
				aria-label={NAMES[color]()}
				title={NAMES[color]()}
				class="relative grid size-8 place-items-center rounded-full border border-(--chip-fg)/30 bg-(--chip-bg) text-(--chip-fg) aria-checked:ring-2 aria-checked:ring-ring aria-checked:ring-offset-2 aria-checked:ring-offset-background max-sm:size-9 max-sm:after:absolute max-sm:after:-inset-[5px]"
				style="--chip-bg: var(--tag-{color}); --chip-fg: var(--tag-{color}-foreground)"
				onclick={() => choose(color)}
			>
				{#if value === color}<Check class="size-4" aria-hidden="true" />{/if}
			</button>
		{/each}
	</div>
	{#if shownMore}
		<div class="flex flex-wrap items-center gap-2 rounded-xl border border-dashed p-2">
			<input
				type="color"
				value={own && value ? value : FALLBACK}
				oninput={(event) => picked(event.currentTarget.value)}
				aria-label={m.color_own_pick()}
				class="h-9 w-11 cursor-pointer rounded-lg border bg-card p-0.5"
			/>
			<Input
				id="{id}-hex"
				value={text}
				oninput={(event) => typed(event.currentTarget.value)}
				maxlength={7}
				spellcheck={false}
				aria-label={m.color_own_hex()}
				aria-invalid={text !== '' && !isHex(text.startsWith('#') ? text : `#${text}`)}
				class={cn('h-9 w-28 font-mono')}
			/>
			<p class="basis-full text-xs text-muted-foreground">{m.color_own_hint()}</p>
		</div>
	{:else}
		<div>
			<Button variant="ghost" size="sm" class="max-sm:h-11" onclick={openMore}>
				{m.color_more()}
			</Button>
		</div>
	{/if}
</div>
