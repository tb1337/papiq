/**
 * What the UI offers. The API checks every call; these rules only keep the UI from showing
 * actions it would reject, and from hiding ones it allows. They follow the endpoint descriptions
 * in `openapi.json`: admins have every right and see everything (Tobi, 08.10.2026).
 */
import type { components } from '#lib/api/schema.ts';

type Document = Pick<components['schemas']['DocumentDetails'], 'owner_id' | 'access'>;
type Actor = {
	id: string;
	role?: components['schemas']['Role'] | null;
	active?: boolean | null;
} | null;
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

/** Move: the owner into spaces they may write to; an admin any document into any space. */
export function canMove(document: Document, user: Actor): boolean {
	return owns(document, user) || isAdmin(user);
}

/** Spaces the caller may file into: own spaces, `read_write` shares; every one for admins. */
export function writableSpaces<T extends { owner_id: string; access: string }>(
	spaces: readonly T[],
	user: Actor,
	admin = isAdmin(user)
): T[] {
	return spaces.filter(
		(space) => admin || space.owner_id === user?.id || space.access === 'read_write'
	);
}

/** Spaces another user may write to, as far as the shares show it (admins see every share):
 * where that user's rule files when an admin changes it. */
export function spacesWritableBy<
	T extends { owner_id: string; shares?: readonly { user_id: string; level: string }[] | null }
>(spaces: readonly T[], owner: Actor): T[] {
	// A deactivated user files nowhere (the API refuses).
	if (owner === null || owner.active === false) return [];
	if (isAdmin(owner)) return [...spaces];
	return spaces.filter(
		(space) =>
			space.owner_id === owner.id ||
			(space.shares ?? []).some(
				(share) => share.user_id === owner.id && share.level === 'read_write'
			)
	);
}

/** Spaces a rule of `owner` may file into when `caller` changes it: the caller's own choice
 * for themselves, else the owner's (an admin changing another user's rule). */
export function spacesFor<
	T extends {
		owner_id: string;
		access: string;
		shares?: readonly { user_id: string; level: string }[] | null;
	}
>(spaces: readonly T[], owner: Actor, caller: Actor): T[] {
	if (owner !== null && owner.id === caller?.id) return writableSpaces(spaces, caller);
	return spacesWritableBy(spaces, owner);
}

/** Rename, share and delete a space: its owner and admins. */
export function canManageSpace(space: Owned, user: Actor): boolean {
	return owns(space, user) || isAdmin(user);
}

/** Change, delete, test a webhook and renew its secret: its owner and admins. */
export function canManageWebhook(webhook: Owned, user: Actor): boolean {
	return owns(webhook, user) || isAdmin(user);
}

/** Change, enable, disable and delete a rule: a user rule's owner, and admins (any rule); the
 * rule of a document type's title pattern nobody (it follows the type). */
export function canChangeRule(
	rule: Owned & { scope: string; managed_by_document_type_id?: string | null },
	user: Actor
): boolean {
	if (rule.managed_by_document_type_id) return false;
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
