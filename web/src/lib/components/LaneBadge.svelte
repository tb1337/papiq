<script lang="ts">
	import CircleCheck from '@lucide/svelte/icons/circle-check';
	import Clock from '@lucide/svelte/icons/clock';
	import OctagonX from '@lucide/svelte/icons/octagon-x';
	import TriangleAlert from '@lucide/svelte/icons/triangle-alert';
	import { m } from '#lib/paraglide/messages.js';
	import { cn } from '#lib/utils.ts';

	type Lane = 'green' | 'yellow' | 'red';

	// A document without a lane is still being processed.
	let { lane, class: className = '' }: { lane: Lane | null; class?: string } = $props();

	const looks = {
		green: {
			icon: CircleCheck,
			label: m.lane_green,
			tone: 'bg-lane-green text-lane-green-foreground'
		},
		yellow: {
			icon: TriangleAlert,
			label: m.lane_yellow,
			tone: 'bg-lane-yellow text-lane-yellow-foreground'
		},
		red: { icon: OctagonX, label: m.lane_red, tone: 'bg-lane-red text-lane-red-foreground' },
		running: {
			icon: Clock,
			label: m.lane_running,
			tone: 'bg-lane-running text-lane-running-foreground'
		}
	};

	const look = $derived(looks[lane ?? 'running']);
</script>

<span
	data-lane={lane ?? 'running'}
	class={cn(
		'inline-flex h-7 items-center gap-1.5 rounded-full pr-3 pl-2.5 text-[0.8125rem] font-semibold whitespace-nowrap',
		look.tone,
		className
	)}
>
	<look.icon class="size-[15px] shrink-0" aria-hidden="true" strokeWidth={2.2} />
	{look.label()}
</span>
