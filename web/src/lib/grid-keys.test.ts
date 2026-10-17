import { describe, expect, it } from 'vitest';
import { nextTile, type TileBox } from './grid-keys.ts';

/** Tiles of 40 px in rows of `columns`; each count in `grids` is a grid of its own below the
 * one before (with a heading between them). */
function layout(columns: number, ...grids: number[]): TileBox[] {
	const boxes: TileBox[] = [];
	let top = 0;
	for (const count of grids) {
		for (let index = 0; index < count; index++) {
			const row = Math.floor(index / columns);
			boxes.push({ top: top + row * 44, left: (index % columns) * 44, width: 40, height: 40 });
		}
		top += Math.ceil(count / columns) * 44 + 30;
	}
	return boxes;
}

describe('nextTile', () => {
	// Two grids of six columns: 0–5, 6–8 | 9–14, 15.
	const boxes = layout(6, 9, 7);

	it('moves left and right in reading order, not beyond the ends', () => {
		expect(nextTile('ArrowRight', 5, boxes)).toBe(6);
		expect(nextTile('ArrowLeft', 6, boxes)).toBe(5);
		expect(nextTile('ArrowLeft', 0, boxes)).toBeNull();
		expect(nextTile('ArrowRight', 15, boxes)).toBeNull();
	});

	it('moves up and down by rows, also from one grid into the next', () => {
		expect(nextTile('ArrowDown', 1, boxes)).toBe(7);
		expect(nextTile('ArrowUp', 7, boxes)).toBe(1);
		// The short row below has no fifth tile: the nearest one.
		expect(nextTile('ArrowDown', 4, boxes)).toBe(8);
		expect(nextTile('ArrowDown', 8, boxes)).toBe(11);
		expect(nextTile('ArrowUp', 9, boxes)).toBe(6);
		expect(nextTile('ArrowDown', 15, boxes)).toBeNull();
		expect(nextTile('ArrowUp', 3, boxes)).toBeNull();
	});

	it('goes to the ends of the row with Home and End, of all with Ctrl', () => {
		expect(nextTile('Home', 11, boxes)).toBe(9);
		expect(nextTile('End', 11, boxes)).toBe(14);
		expect(nextTile('End', 15, boxes)).toBeNull();
		expect(nextTile('Home', 11, boxes, true)).toBe(0);
		expect(nextTile('End', 3, boxes, true)).toBe(15);
	});

	it('ignores other keys', () => {
		expect(nextTile('Enter', 3, boxes)).toBeNull();
		expect(nextTile('ArrowDown', 99, boxes)).toBeNull();
	});
});
