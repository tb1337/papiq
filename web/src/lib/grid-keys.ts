/**
 * Moving the focus with the keyboard in a grid of tiles that wraps into rows (the icon picker):
 * one tab stop for the whole grid (roving tabindex), the arrows, Home and End move it. Where the
 * rows break follows from the layout, so the boxes of the tiles decide.
 */

/** A tile's box, as `getBoundingClientRect` gives it. */
export interface TileBox {
	top: number;
	left: number;
	width: number;
	height: number;
}

/** The keys `nextTile` moves by. */
export const GRID_KEYS: ReadonlySet<string> = new Set([
	'ArrowLeft',
	'ArrowRight',
	'ArrowUp',
	'ArrowDown',
	'Home',
	'End'
]);

/** The tiles (by index) in rows, top to bottom: a tile starts a new row when it stands lower
 * than half its height below the row's first one. Tiles come in reading order. */
function rows(boxes: readonly TileBox[]): number[][] {
	const found: number[][] = [];
	boxes.forEach((box, index) => {
		const row = found[found.length - 1];
		if (row && Math.abs(box.top - boxes[row[0]].top) < Math.max(box.height, 1) / 2) {
			row.push(index);
		} else {
			found.push([index]);
		}
	});
	return found;
}

/** Where `key` moves the focus from tile `index`: left and right to the previous and next
 * tile, up and down to the tile of the row above or below whose middle is nearest (rows of
 * several grids follow each other), Home and End to the first and last of its row, with
 * `all` (Ctrl or ⌘) of all tiles. Null for another key, or where it cannot go further. */
export function nextTile(
	key: string,
	index: number,
	boxes: readonly TileBox[],
	all = false
): number | null {
	if (index < 0 || index >= boxes.length) return null;
	const last = boxes.length - 1;
	const lines = rows(boxes);
	const line = lines.findIndex((row) => row.includes(index));
	const row = lines[line];
	let next: number | null = null;
	switch (key) {
		case 'ArrowLeft':
			next = index > 0 ? index - 1 : null;
			break;
		case 'ArrowRight':
			next = index < last ? index + 1 : null;
			break;
		case 'Home':
			next = all ? 0 : row[0];
			break;
		case 'End':
			next = all ? last : row[row.length - 1];
			break;
		case 'ArrowUp':
		case 'ArrowDown': {
			const target = lines[line + (key === 'ArrowUp' ? -1 : 1)];
			if (!target) break;
			const middle = (box: TileBox) => box.left + box.width / 2;
			const from = middle(boxes[index]);
			next = target.reduce((best, candidate) =>
				Math.abs(middle(boxes[candidate]) - from) < Math.abs(middle(boxes[best]) - from)
					? candidate
					: best
			);
			break;
		}
	}
	return next === index ? null : next;
}
