/**
 * What the UI offers. The API checks every call; these rules only keep the UI from showing
 * actions it would reject, and from hiding ones it allows. They follow the endpoint descriptions
 * in `openapi.json`: admins have every right and see everything (Tobi, 08.10.2026).
 */
import type { components } from '#lib/api/schema.ts';

type Document = Pick<components['schemas']['DocumentDetails'], 'owner_id' | 'access'>;
type Actor = { id: string; role?: components['schemas']['Role'] | null } | null;
type Owned = { owner_id: string | null };

const isAdmin = (user: Actor): boolean => user?.role === 'admin';
const owns = (item: Owned, user: Actor): boolean => user !== null && item.owner_id === user.id;

/** Change the metadata: the owner, a `read_write` share, an admin (their access is `read_write`). */
export function canEdit(document: Document, user: Actor): boolean {
	return user !== null && (owns(document, user) || document.access === 'read_write');
}

/** Retry, reprocess, delete, read the processing log, review: the owner and admins. */
export function canManage(document: Document, user: Actor): boolean {
	return owns(document, user) || isAdmin(user);
}

/** Move: the owner into drawers they may write to; an admin any document into any drawer. */
export function canMove(document: Document, user: Actor): boolean {
	return owns(document, user) || isAdmin(user);
}

/** Drawers the caller may file into: own drawers, `read_write` shares; every one for admins. */
export function writableDrawers<T extends { owner_id: string; access: string }>(
	drawers: readonly T[],
	user: Actor,
	admin = isAdmin(user)
): T[] {
	return drawers.filter(
		(drawer) => admin || drawer.owner_id === user?.id || drawer.access === 'read_write'
	);
}

/** Drawers another user may write to, as far as the shares show it (admins see every share):
 * where an admin files that user's document or that user's rule files. */
export function drawersWritableBy<
	T extends { owner_id: string; shares?: readonly { user_id: string; level: string }[] | null }
>(drawers: readonly T[], owner: Actor): T[] {
	if (owner === null) return [];
	if (isAdmin(owner)) return [...drawers];
	return drawers.filter(
		(drawer) =>
			drawer.owner_id === owner.id ||
			(drawer.shares ?? []).some(
				(share) => share.user_id === owner.id && share.level === 'read_write'
			)
	);
}

/** Rename, share and delete a drawer: its owner and admins. */
export function canManageDrawer(drawer: Owned, user: Actor): boolean {
	return owns(drawer, user) || isAdmin(user);
}

/** Change, delete, test a webhook and renew its secret: its owner and admins. */
export function canManageWebhook(webhook: Owned, user: Actor): boolean {
	return owns(webhook, user) || isAdmin(user);
}

/** Change, enable, disable and delete a rule: a user rule's owner, and admins (any rule). */
export function canChangeRule(rule: Owned & { scope: string }, user: Actor): boolean {
	if (isAdmin(user)) return true;
	return rule.scope === 'user' && owns(rule, user);
}

/** Apply a rule to existing documents: a user rule's owner and admins; a global rule anyone. */
export function canApplyRule(rule: Owned & { scope: string }, user: Actor): boolean {
	return user !== null && (rule.scope === 'global' || owns(rule, user) || isAdmin(user));
}

/** Lists of every user (documents, inbox, search, rules): admins only. */
export function canListAllUsers(user: Actor): boolean {
	return isAdmin(user);
}
