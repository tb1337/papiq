import { describe, expect, it } from 'vitest';
import { canEdit, canManage, canMove, writableDrawers } from './permissions.ts';

const owner = { id: 'u1', role: 'user' as const };
const other = { id: 'u2', role: 'user' as const };
const admin = { id: 'u3', role: 'admin' as const };
const own = { owner_id: 'u1', access: 'read_write' as const };
const sharedRead = { owner_id: 'u1', access: 'read' as const };
const sharedWrite = { owner_id: 'u1', access: 'read_write' as const };

describe('document rights', () => {
	it('lets the owner do everything', () => {
		expect(canEdit(own, owner)).toBe(true);
		expect(canManage(own, owner)).toBe(true);
		expect(canMove(own, owner)).toBe(true);
	});

	it('lets a read share only read', () => {
		expect(canEdit(sharedRead, other)).toBe(false);
		expect(canManage(sharedRead, other)).toBe(false);
		expect(canMove(sharedRead, other)).toBe(false);
	});

	it('lets a write share edit but not manage or move', () => {
		expect(canEdit(sharedWrite, other)).toBe(true);
		expect(canManage(sharedWrite, other)).toBe(false);
		expect(canMove(sharedWrite, other)).toBe(false);
	});

	it('lets an admin move any document, nothing else on top', () => {
		expect(canMove(sharedRead, admin)).toBe(true);
		expect(canManage(sharedRead, admin)).toBe(false);
		expect(canEdit(sharedRead, admin)).toBe(false);
	});

	it('offers nothing without a user', () => {
		expect(canEdit(own, null)).toBe(false);
		expect(canMove(own, null)).toBe(false);
	});
});

describe('writableDrawers', () => {
	const drawers = [
		{ id: 'a', owner_id: 'u1', access: 'read_write' },
		{ id: 'b', owner_id: 'u9', access: 'read' },
		{ id: 'c', owner_id: 'u9', access: 'read_write' }
	];

	it('keeps own and write-shared drawers', () => {
		expect(writableDrawers(drawers, owner).map((drawer) => drawer.id)).toEqual(['a', 'c']);
	});

	it('gives an admin all of them', () => {
		expect(writableDrawers(drawers, admin)).toHaveLength(3);
	});
});
