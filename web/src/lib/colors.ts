/**
 * Colours of tags: twelve of the palette, tuned for light and dark in `app.css`
 * (`--tag-<name>` and `--tag-<name>-foreground`, each pair at least 4.5:1), or an own one as
 * `#rrggbb`. A chip mixes an own colour with the card (22 %) for its ground and with the text
 * colour for its writing, in Oklab as the CSS does (`.chip-own` in `app.css`); as much of the
 * colour goes into the writing as keeps it readable, in light and in dark, so the writing
 * follows the contrast (WCAG AA, 4.5:1, with a margin).
 */

export const PALETTE = [
	'gray',
	'red',
	'orange',
	'amber',
	'lime',
	'green',
	'teal',
	'sky',
	'blue',
	'violet',
	'fuchsia',
	'pink'
] as const;
export type PaletteColor = (typeof PALETTE)[number];

const HEX = /^#[0-9a-f]{6}$/i;

export function isPalette(color: string | null | undefined): color is PaletteColor {
	return (PALETTE as readonly string[]).includes(color ?? '');
}

export function isHex(color: string | null | undefined): boolean {
	return HEX.test(color ?? '');
}

/** The ground and text colours of the themes (`app.css`: `--card`, `--foreground`). */
export const THEMES = {
	light: { card: '#ffffff', text: '#1b1a17' },
	dark: { card: '#181c1b', text: '#ebeae6' }
} as const;
export type Theme = keyof typeof THEMES;

/** Of an own colour, the ground of a chip. */
export const GROUND_SHARE = 0.22;
/** Of an own colour, the most that goes into the writing. */
export const WRITING_SHARE = 0.7;
/** The contrast aimed at: WCAG AA (4.5:1) with a margin for rounding in the browser. */
export const AIM = 4.6;

type Rgb = readonly [number, number, number];

export function parseHex(hex: string): Rgb {
	const value = hex.slice(1);
	return [0, 2, 4].map((at) => parseInt(value.slice(at, at + 2), 16)) as unknown as Rgb;
}

function toLinear(channel: number): number {
	const c = channel / 255;
	return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function fromLinear(value: number): number {
	const c = Math.min(1, Math.max(0, value));
	return 255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
}

function toOklab([r, g, b]: Rgb): Rgb {
	const [lr, lg, lb] = [toLinear(r), toLinear(g), toLinear(b)];
	const l = Math.cbrt(0.4122214708 * lr + 0.5363325363 * lg + 0.0514459929 * lb);
	const m = Math.cbrt(0.2119034982 * lr + 0.6806995451 * lg + 0.1073969566 * lb);
	const s = Math.cbrt(0.0883024619 * lr + 0.2817188376 * lg + 0.6299787005 * lb);
	return [
		0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
		1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
		0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s
	];
}

function fromOklab([lightness, a, b]: Rgb): Rgb {
	const l = (lightness + 0.3963377774 * a + 0.2158037573 * b) ** 3;
	const m = (lightness - 0.1055613458 * a - 0.0638541728 * b) ** 3;
	const s = (lightness - 0.0894841775 * a - 1.291485548 * b) ** 3;
	return [
		fromLinear(4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s),
		fromLinear(-1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s),
		fromLinear(-0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s)
	];
}

/** `color-mix(in oklab, first share, second)`. */
export function mix(first: Rgb, second: Rgb, share: number): Rgb {
	const [a, b] = [toOklab(first), toOklab(second)];
	return fromOklab([0, 1, 2].map((i) => share * a[i] + (1 - share) * b[i]) as unknown as Rgb);
}

function luminance(rgb: Rgb): number {
	const [r, g, b] = rgb.map((channel) => toLinear(Math.round(channel)));
	return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** The WCAG contrast of two colours. */
export function contrast(first: Rgb, second: Rgb): number {
	const [a, b] = [luminance(first), luminance(second)];
	return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

/** How much of `hex` goes into the writing of a chip in `theme`: the most, in steps of 5 %
 * from `WRITING_SHARE` down, that keeps `AIM` against the chip's ground. */
export function writingShare(hex: string, theme: Theme): number {
	const own = parseHex(hex);
	const { card, text } = THEMES[theme];
	const ground = mix(own, parseHex(card), GROUND_SHARE);
	for (let step = Math.round(WRITING_SHARE * 20); step > 0; step--) {
		const share = step / 20;
		if (contrast(mix(own, parseHex(text), share), ground) >= AIM) return share;
	}
	return 0;
}

/** The chip's colours of a tag, as a style attribute (null without a colour): a palette
 * colour's tokens, or an own colour with the shares of each theme for `.chip-own`. */
export function chipStyle(color: string | null | undefined): string | null {
	if (isPalette(color)) {
		return `--chip-bg: var(--tag-${color}); --chip-fg: var(--tag-${color}-foreground)`;
	}
	if (color && isHex(color)) {
		const hex = color.toLowerCase();
		const share = (theme: Theme) => `${Math.round(writingShare(hex, theme) * 100)}%`;
		return `--chip-own: ${hex}; --chip-light: ${share('light')}; --chip-dark: ${share('dark')}`;
	}
	return null;
}
