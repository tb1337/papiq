/**
 * What the UI offers on a document. The API checks every call; these rules only keep the UI from
 * showing actions it would reject. They follow the endpoint descriptions in `openapi.json`.
 */
import type { components } from '#lib/api/schema.ts';

type Document = Pick<components['schemas']['DocumentDetails'], 'owner_id' | 'access'>;
type Actor = { id: string; role?: components['schemas']['Role'] | null } | null;

const isOwner = (document: Document, user: Actor): boolean =>
	user !== null && document.owner_id === user.id;

/** Change the metadata: the owner, or a `read_write` share (the owner's access is `read_write`). */
export function canEdit(document: Document, user: Actor): boolean {
	return user !== null && (isOwner(document, user) || document.access === 'read_write');
}

/** Retry, reprocess and delete are for the owner only. */
export function canManage(document: Document, user: Actor): boolean {
	return isOwner(document, user);
}

/** Move: the owner into drawers they may write to; an admin any document into any drawer. */
export function canMove(document: Document, user: Actor): boolean {
	return isOwner(document, user) || user?.role === 'admin';
}

/** Drawers the caller may move into: own drawers and those shared with `read_write`. */
export function writableDrawers<T extends { owner_id: string; access: string }>(
	drawers: readonly T[],
	user: Actor,
	admin = user?.role === 'admin'
): T[] {
	return drawers.filter(
		(drawer) => admin || drawer.owner_id === user?.id || drawer.access === 'read_write'
	);
}
