import { render, screen } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import { m } from '#lib/paraglide/messages.js';
import LaneBadge from './LaneBadge.svelte';

describe('LaneBadge', () => {
	it.each([
		['green', m.lane_green()],
		['yellow', m.lane_yellow()],
		['red', m.lane_red()]
	] as const)('names the %s lane in words', (lane, label) => {
		render(LaneBadge, { lane });
		const badge = screen.getByText(label);
		expect(badge.dataset.lane).toBe(lane);
		// Not by colour alone: an icon goes with the word.
		expect(badge.querySelector('svg')).not.toBeNull();
	});

	it('shows a document without a lane as running', () => {
		render(LaneBadge, { lane: null });
		expect(screen.getByText(m.lane_running()).dataset.lane).toBe('running');
	});
});
