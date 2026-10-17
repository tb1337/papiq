/**
 * Creating a tag where tags are picked, for admins: a picker's search that finds no tag of its
 * kind named so offers "“…” anlegen" (at the top level, with the defaults of the kind, picked at
 * once) and "Mit Details anlegen …" (the tag dialog over the current one, the kind set and the
 * name filled in). Either way the lists and lookups showing master data load again.
 */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import { reportError } from '#lib/errors.ts';
import { masterDataDialogs } from '#lib/masterdata-dialog.svelte.ts';
import type { Tag, TagScope } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { ofScope } from '#lib/tags.ts';
import { toast } from 'svelte-sonner';

/** Whether a picker's `query` may become a new tag of `scope`: something typed that no tag of
 * the kind is named already (regardless of case and surrounding spaces). */
export function creatable(tags: readonly Tag[], scope: TagScope, query: string): boolean {
	const name = query.trim().toLocaleLowerCase();
	return (
		name !== '' && !ofScope(tags, scope).some((tag) => tag.name.trim().toLocaleLowerCase() === name)
	);
}

/** A tag of `scope` named `name` at the top level, with its kind's defaults; null (the problem
 * shown) when the API refuses. */
export async function createQuickly(scope: TagScope, name: string): Promise<Tag | null> {
	try {
		const tag = await unwrap(api.POST('/api/v1/tags', { body: { name: name.trim(), scope } }));
		toast.success(m.tag_created({ name: tag.name }));
		masterDataDialogs.saved();
		return tag;
	} catch (error) {
		reportError(error);
		return null;
	}
}

/** The tag dialog over the current one for a tag of `scope`, named `name` to start with: the
 * tag made, or null. */
export function createWithDetails(scope: TagScope, name: string): Promise<Tag | null> {
	return masterDataDialogs.createTag(scope, name.trim());
}
