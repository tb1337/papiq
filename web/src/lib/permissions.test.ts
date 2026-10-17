import { describe, expect, it } from 'vitest';
import {
	canApplyRule,
	canChangeRule,
	canEdit,
	canListAllUsers,
	canManage,
	canManageSpace,
	canManageWebhook,
	canMove,
	spacesFor,
	spacesWritableBy,
	writableSpaces
} from './permissions.ts';

const owner = { id: 'u1', role: 'user' as const };
const other = { id: 'u2', role: 'user' as const };
const admin = { id: 'u3', role: 'admin' as const };
const own = { owner_id: 'u1', access: 'read_write' as const };
const sharedRead = { owner_id: 'u1', access: 'read' as const };
const sharedWrite = { owner_id: 'u1', access: 'read_write' as const };
const asAdmin = { owner_id: 'u1', access: 'read_write' as const };

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

	it('lets an admin do everything (the API gives them read_write)', () => {
		expect(canEdit(asAdmin, admin)).toBe(true);
		expect(canManage(asAdmin, admin)).toBe(true);
		expect(canMove(asAdmin, admin)).toBe(true);
	});

	it('offers nothing without a user', () => {
		expect(canEdit(own, null)).toBe(false);
		expect(canManage(own, null)).toBe(false);
		expect(canMove(own, null)).toBe(false);
	});
});

describe('spaces, webhooks, lists', () => {
	const space = { owner_id: 'u1' };

	it('lets the owner and admins manage a space or a webhook', () => {
		for (const check of [canManageSpace, canManageWebhook]) {
			expect(check(space, owner)).toBe(true);
			expect(check(space, admin)).toBe(true);
			expect(check(space, other)).toBe(false);
			expect(check(space, null)).toBe(false);
		}
	});

	it('lets only admins list every user', () => {
		expect(canListAllUsers(admin)).toBe(true);
		expect(canListAllUsers(owner)).toBe(false);
		expect(canListAllUsers(null)).toBe(false);
	});
});

describe('rules', () => {
	const mine = { owner_id: 'u1', scope: 'user' };
	const global = { owner_id: null, scope: 'global' };

	it('lets the owner change a user rule, admins any rule', () => {
		expect(canChangeRule(mine, owner)).toBe(true);
		expect(canChangeRule(mine, other)).toBe(false);
		expect(canChangeRule(mine, admin)).toBe(true);
		expect(canChangeRule(global, owner)).toBe(false);
		expect(canChangeRule(global, admin)).toBe(true);
		// The rule of a title pattern follows its document type: nobody changes it here.
		const pattern = { ...global, managed_by_document_type_id: 'type' };
		expect(canChangeRule(pattern, admin)).toBe(false);
	});

	it('lets anyone apply a global rule, a user rule its owner and admins', () => {
		expect(canApplyRule(global, other)).toBe(true);
		expect(canApplyRule(mine, owner)).toBe(true);
		expect(canApplyRule(mine, admin)).toBe(true);
		expect(canApplyRule(mine, other)).toBe(false);
		expect(canApplyRule(global, null)).toBe(false);
	});
});

describe('writableSpaces', () => {
	const spaces = [
		{ id: 'a', owner_id: 'u1', access: 'read_write' },
		{ id: 'b', owner_id: 'u9', access: 'read' },
		{ id: 'c', owner_id: 'u9', access: 'read_write' }
	];

	it('keeps own and write-shared spaces', () => {
		expect(writableSpaces(spaces, owner).map((space) => space.id)).toEqual(['a', 'c']);
	});

	it('gives an admin all of them', () => {
		expect(writableSpaces(spaces, admin)).toHaveLength(3);
	});
});

describe('spacesWritableBy', () => {
	const spaces = [
		{ id: 'a', owner_id: 'u1', shares: null },
		{ id: 'b', owner_id: 'u9', shares: [{ user_id: 'u1', level: 'read' }] },
		{ id: 'c', owner_id: 'u9', shares: [{ user_id: 'u1', level: 'read_write' }] },
		{ id: 'd', owner_id: 'u9', shares: [] }
	];

	it('keeps the spaces the given user owns or may write to by share', () => {
		expect(spacesWritableBy(spaces, owner).map((space) => space.id)).toEqual(['a', 'c']);
	});

	it('keeps every space for an admin and none for nobody', () => {
		expect(spacesWritableBy(spaces, { id: 'u1', role: 'admin' })).toHaveLength(4);
		expect(spacesWritableBy(spaces, null)).toEqual([]);
		expect(spacesWritableBy(spaces, { id: 'u1', role: 'admin', active: false })).toEqual([]);
	});
});

describe('spacesFor', () => {
	const spaces = [
		{ id: 'a', owner_id: 'u1', access: 'read_write', shares: null },
		{ id: 'b', owner_id: 'u2', access: 'read', shares: [] },
		{ id: 'c', owner_id: 'u3', access: 'read', shares: null }
	];

	it("offers the caller their own choice, and an admin acting for a user that user's", () => {
		expect(spacesFor(spaces, owner, owner).map((space) => space.id)).toEqual(['a']);
		expect(spacesFor(spaces, admin, admin)).toHaveLength(3);
		expect(spacesFor(spaces, other, admin).map((space) => space.id)).toEqual(['b']);
		expect(spacesFor(spaces, null, admin)).toEqual([]);
	});
});
