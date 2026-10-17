import { render, screen, waitFor } from '@testing-library/svelte';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { CURATED, CURATED_NAMES } from '#lib/icons/curated.ts';
import { m } from '#lib/paraglide/messages.js';
import IconPicker from './IconPicker.svelte';

// The icon picker's panel is loaded when it opens; under a full parallel run that can take a
// while.
const LOADED = { timeout: 5000 };

describe('IconPicker', () => {
	afterEach(() => vi.restoreAllMocks());

	it('offers the icons by area and takes one', async () => {
		const user = userEvent.setup();
		render(IconPicker, { id: 'icon', value: null });
		expect(screen.getByText(m.icon_none())).toBeTruthy();
		await user.click(screen.getByRole('button', { name: m.icon_choose() }));
		// The panel loads when the picker opens.
		expect(await screen.findByRole('region', { name: m.icon_area_money() }, LOADED)).toBeTruthy();
		expect(screen.getAllByRole('button', { pressed: false }).length).toBeGreaterThanOrEqual(170);
		await user.click(screen.getByTitle('piggy-bank'));
		expect(screen.getByText('piggy-bank')).toBeTruthy();
		// Closed again; the icon is drawn once it is loaded.
		expect(screen.queryByRole('region', { name: m.icon_area_money() })).toBeNull();
		await waitFor(() => expect(document.querySelector('svg.lucide-piggy-bank')).not.toBeNull());
	});

	it('searches every icon and removes the chosen one', async () => {
		const user = userEvent.setup();
		render(IconPicker, { id: 'icon', value: 'house' });
		await user.click(screen.getByRole('button', { name: m.icon_choose() }));
		await user.type(await screen.findByRole('searchbox', {}, LOADED), 'rocket');
		expect(screen.queryByRole('region')).toBeNull();
		expect(screen.getByTitle('rocket')).toBeTruthy();
		await user.clear(screen.getByRole('searchbox'));
		await user.type(screen.getByRole('searchbox'), 'zzzz');
		expect(screen.getByText(m.icon_search_none())).toBeTruthy();
		await user.click(screen.getByRole('button', { name: m.icon_done() }));
		await user.click(screen.getByRole('button', { name: m.icon_remove() }));
		expect(screen.getByText(m.icon_none())).toBeTruthy();
	});

	it('is one tab stop whose arrows move by rows, Enter choosing', async () => {
		// Six tiles to a row: jsdom lays nothing out.
		vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function (
			this: HTMLElement
		) {
			const tiles = [...(this.parentElement?.children ?? [])];
			const grids = [...document.querySelectorAll('[data-icon-tile]')].map(
				(tile) => tile.parentElement
			);
			const grid = [...new Set(grids)].indexOf(this.parentElement);
			const index = tiles.indexOf(this);
			const top = grid * 1000 + Math.floor(index / 6) * 44;
			return { top, left: (index % 6) * 44, width: 40, height: 40 } as DOMRect;
		});
		const user = userEvent.setup();
		render(IconPicker, { id: 'icon', value: 'files' });
		await user.click(screen.getByRole('button', { name: m.icon_choose() }));
		const chosen = await screen.findByTitle('files', {}, LOADED);
		const tiles = [...document.querySelectorAll<HTMLElement>('[data-icon-tile]')];
		expect(tiles.filter((tile) => tile.tabIndex === 0)).toEqual([chosen]);
		await user.click(screen.getByRole('searchbox'));
		await user.tab();
		expect(document.activeElement).toBe(chosen);
		const at = (title: string) => expect(document.activeElement?.getAttribute('title')).toBe(title);
		await user.keyboard('{ArrowRight}');
		at(CURATED[0].icons[2]);
		await user.keyboard('{ArrowDown}');
		at(CURATED[0].icons[8]);
		await user.keyboard('{End}');
		at(CURATED[0].icons[11]);
		await user.keyboard('{Home}');
		at(CURATED[0].icons[6]);
		await user.keyboard('{ArrowLeft}');
		at(CURATED[0].icons[5]);
		await user.keyboard('{Control>}{End}{/Control}');
		at(CURATED_NAMES[CURATED_NAMES.length - 1]);
		// The tab stop follows; Tab leaves the tiles.
		expect(tiles.filter((tile) => tile.tabIndex === 0)).toEqual([document.activeElement]);
		await user.tab();
		expect(document.activeElement?.hasAttribute('data-icon-tile')).toBe(false);
		await user.tab({ shift: true });
		at(CURATED_NAMES[CURATED_NAMES.length - 1]);
		// Enter chooses, closes and gives the focus back to the button.
		await user.keyboard('{Control>}{Home}{/Control}{Enter}');
		expect(screen.getByText(CURATED_NAMES[0])).toBeTruthy();
		expect(document.activeElement).toBe(screen.getByRole('button', { name: m.icon_choose() }));
	});

	it('marks an unknown name with a stand-in', async () => {
		render(IconPicker, { id: 'icon', value: 'gone-icon' });
		await waitFor(() => expect(document.querySelector('[data-unknown-icon]')).not.toBeNull());
	});
});
