import { describe, expect, it } from 'vitest';
import { PagedList } from './paging.svelte.ts';

const pages: Record<string, { items: number[]; next: string | null }> = {
	start: { items: [1, 2], next: 'two' },
	two: { items: [3], next: null }
};

function list(fail = false) {
	return new PagedList<number>(async (after) => {
		if (fail) throw new Error('boom');
		return pages[(after as string | null) ?? 'start'];
	});
}

describe('PagedList', () => {
	it('loads page by page', async () => {
		const items = list();
		await items.reload();
		expect(items.items).toEqual([1, 2]);
		expect(items.hasMore).toBe(true);
		await items.more();
		expect(items.items).toEqual([1, 2, 3]);
		expect(items.hasMore).toBe(false);
	});

	it('knows when it is empty', async () => {
		const items = new PagedList<number>(async () => ({ items: [], next: null }));
		expect(items.empty).toBe(false);
		await items.reload();
		expect(items.empty).toBe(true);
	});

	it('keeps the error', async () => {
		const items = list(true);
		await items.reload();
		expect(items.error).toBeInstanceOf(Error);
		expect(items.empty).toBe(false);
	});

	it('refreshes the pages shown without emptying the list', async () => {
		const items = list();
		await items.reload();
		await items.more();
		await items.refresh();
		expect(items.items).toEqual([1, 2, 3]);
	});

	it('starts over on reload', async () => {
		const items = list();
		await items.reload();
		await items.more();
		await items.reload();
		expect(items.items).toEqual([1, 2]);
	});
});
