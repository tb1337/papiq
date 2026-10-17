import { render, screen } from '@testing-library/svelte';
import { userEvent } from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { m } from '#lib/paraglide/messages.js';
import ColorPicker from './ColorPicker.svelte';

describe('ColorPicker', () => {
	it('chooses one of twelve colours or none', async () => {
		const user = userEvent.setup();
		render(ColorPicker, { id: 'color', value: null });
		const radios = screen.getAllByRole('radio');
		expect(radios.length).toBe(13);
		expect(screen.getByRole('radio', { name: m.color_none() }).getAttribute('aria-checked')).toBe(
			'true'
		);
		await user.click(screen.getByRole('radio', { name: m.color_teal() }));
		expect(screen.getByRole('radio', { name: m.color_teal() }).getAttribute('aria-checked')).toBe(
			'true'
		);
		expect(screen.getByRole('radio', { name: m.color_teal() }).getAttribute('style')).toContain(
			'var(--tag-teal)'
		);
	});

	it('moves the choice with the arrow keys', async () => {
		const user = userEvent.setup();
		render(ColorPicker, { id: 'color', value: 'pink' });
		const pink = screen.getByRole('radio', { name: m.color_pink() });
		expect(pink.getAttribute('tabindex')).toBe('0');
		expect(screen.getByRole('radio', { name: m.color_red() }).getAttribute('tabindex')).toBe('-1');
		pink.focus();
		await user.keyboard('{ArrowRight}');
		const none = screen.getByRole('radio', { name: m.color_none() });
		expect(none.getAttribute('aria-checked')).toBe('true');
		expect(document.activeElement).toBe(none);
		await user.keyboard('{ArrowLeft}{ArrowLeft}');
		expect(
			screen.getByRole('radio', { name: m.color_fuchsia() }).getAttribute('aria-checked')
		).toBe('true');
	});

	it('takes an own colour as #rrggbb behind "More colours"', async () => {
		const user = userEvent.setup();
		render(ColorPicker, { id: 'color', value: 'red' });
		expect(screen.queryByLabelText(m.color_own_hex())).toBeNull();
		await user.click(screen.getByRole('button', { name: m.color_more() }));
		const hex = screen.getByLabelText(m.color_own_hex()) as HTMLInputElement;
		expect(hex.value).toBe('#b45309');
		expect(
			screen.getAllByRole('radio').every((radio) => radio.getAttribute('aria-checked') === 'false')
		).toBe(true);
		await user.clear(hex);
		await user.type(hex, '#12AB3');
		expect(hex.getAttribute('aria-invalid')).toBe('true');
		await user.type(hex, 'f');
		expect(hex.getAttribute('aria-invalid')).toBe('false');
		const picker = screen.getByLabelText(m.color_own_pick()) as HTMLInputElement;
		expect(picker.value).toBe('#12ab3f');
	});

	it('opens with an own colour shown', () => {
		render(ColorPicker, { id: 'color', value: '#a1b2c3' });
		expect((screen.getByLabelText(m.color_own_hex()) as HTMLInputElement).value).toBe('#a1b2c3');
	});
});
