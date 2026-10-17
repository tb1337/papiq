import { describe, expect, it } from 'vitest';
import CSS from '../app.css?raw';
import {
	AIM,
	chipStyle,
	contrast,
	GROUND_SHARE,
	isHex,
	isPalette,
	mix,
	PALETTE,
	parseHex,
	THEMES,
	writingShare,
	type Theme
} from './colors.ts';

/** The custom properties of the first block that `selector` opens with `{`. */
function tokens(selector: ':root' | '.dark'): Map<string, string> {
	const found = new Map<string, string>();
	const pattern = new RegExp(`^${selector.replace('.', '\\.')} \\{([^}]*)\\}`, 'gm');
	for (const block of CSS.matchAll(pattern)) {
		for (const [, name, value] of block[1].matchAll(/--([\w-]+):\s*([^;]+);/g)) {
			if (!found.has(name)) found.set(name, value.trim());
		}
	}
	return found;
}

const THEME_TOKENS: Record<Theme, Map<string, string>> = {
	light: tokens(':root'),
	dark: tokens('.dark')
};

function writing(hex: string, theme: Theme) {
	const own = parseHex(hex);
	const { card, text } = THEMES[theme];
	const ground = mix(own, parseHex(card), GROUND_SHARE);
	return contrast(mix(own, parseHex(text), writingShare(hex, theme)), ground);
}

describe('colors', () => {
	it.each(['light', 'dark'] as const)('the palette meets AA in %s', (theme) => {
		const found = THEME_TOKENS[theme];
		for (const color of PALETTE) {
			const ground = found.get(`tag-${color}`);
			const text = found.get(`tag-${color}-foreground`);
			expect(ground, color).toMatch(/^#[0-9a-f]{6}$/);
			expect(text, color).toMatch(/^#[0-9a-f]{6}$/);
			expect(contrast(parseHex(ground!), parseHex(text!)), color).toBeGreaterThanOrEqual(4.5);
		}
	});

	it.each(['light', 'dark'] as const)('mixes against the colours of %s', (theme) => {
		expect(THEME_TOKENS[theme].get('card')).toBe(THEMES[theme].card);
		expect(THEME_TOKENS[theme].get('foreground')).toBe(THEMES[theme].text);
	});

	it('keeps own colours readable in light and dark', () => {
		const steps = [0, 51, 102, 153, 204, 255];
		let worst = Infinity;
		for (const r of steps)
			for (const g of steps)
				for (const b of steps) {
					const hex = `#${[r, g, b].map((c) => c.toString(16).padStart(2, '0')).join('')}`;
					for (const theme of ['light', 'dark'] as const) {
						worst = Math.min(worst, writing(hex, theme));
					}
				}
		expect(worst).toBeGreaterThanOrEqual(AIM);
		// Extremes the 70 % of the approved preview would make unreadable.
		expect(writing('#ffffff', 'light')).toBeGreaterThanOrEqual(4.5);
		expect(writing('#000000', 'dark')).toBeGreaterThanOrEqual(4.5);
		expect(writingShare('#ffffff', 'light')).toBeLessThan(0.7);
		// A middle colour keeps the preview's share.
		expect(writingShare('#b45309', 'light')).toBe(0.7);
	});

	it('names the chip colours as a style', () => {
		expect(chipStyle('red')).toBe(
			'--chip-bg: var(--tag-red); --chip-fg: var(--tag-red-foreground)'
		);
		expect(chipStyle('#B45309')).toMatch(
			/^--chip-own: #b45309; --chip-light: \d+%; --chip-dark: \d+%$/
		);
		expect(chipStyle(null)).toBeNull();
		expect(chipStyle('purple')).toBeNull();
		expect(isPalette('teal') && !isPalette('#123456')).toBe(true);
		expect(isHex('#a1B2c3') && !isHex('#a1b2c') && !isHex('teal')).toBe(true);
	});
});
