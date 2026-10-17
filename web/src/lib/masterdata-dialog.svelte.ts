/**
 * The dialogs that add and change master data, opened from anywhere: one host in the app's
 * layout shows them (`MasterDataDialogHost`). Lists watch `changed` and load again. A picker
 * makes a tag with its details in a second dialog over the one it is in (`createTag`).
 */
import type { FieldDefinition, Tag, TagScope } from '#lib/masterdata.svelte.ts';
import type { Item, SimpleKind } from '#lib/masterdata-api.ts';

/** `any-tag`: a new tag whose kind is chosen in the dialog first. */
export type MasterDataKind = SimpleKind | 'fields' | 'any-tag';

export interface MasterDataRequest {
	kind: MasterDataKind;
	/** The item to change, or its id (it is loaded); null adds one. */
	item: Item | FieldDefinition | string | null;
	/** Opened from this rule (the rule of a type's title pattern): no link back to it. */
	fromRule: string | null;
}

/** A tag to make with its details over the dialog a picker is in: of `scope`, named `name`
 * for a start; `resolve` gets the tag made, or null. */
export interface TagRequest {
	scope: TagScope;
	name: string;
	resolve: (tag: Tag | null) => void;
}

class MasterDataDialogs {
	request = $state<MasterDataRequest | null>(null);
	nested = $state.raw<TagRequest | null>(null);
	/** Counts saves; a list that shows master data loads again when it changes. */
	changed = $state(0);

	open(
		kind: MasterDataKind,
		item: Item | FieldDefinition | string | null = null,
		options: { fromRule?: string } = {}
	) {
		this.request = { kind, item, fromRule: options.fromRule ?? null };
	}

	close() {
		this.request = null;
	}

	saved() {
		this.changed += 1;
	}

	/** The tag dialog over the current one, the kind set, the name filled in: the tag made, or
	 * null once it closes without. */
	createTag(scope: TagScope, name: string): Promise<Tag | null> {
		this.nested?.resolve(null);
		return new Promise((resolve) => {
			this.nested = { scope, name, resolve };
		});
	}

	/** The tag dialog closed: with the tag it made, or without (null). */
	finishTag(tag: Tag | null) {
		const nested = this.nested;
		this.nested = null;
		nested?.resolve(tag);
	}
}

export const masterDataDialogs = new MasterDataDialogs();
