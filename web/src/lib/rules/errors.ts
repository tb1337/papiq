/**
 * Errors of saving a rule, next to the block they concern. The API names the place in a 422
 * (`conditions.all[1].any[0]: …`, `actions[2]: …`; request validation also names schema parts
 * in between), the missing id in a 404 (`tag <id> not found`) and the drawer in a 403.
 */
import { ApiError } from '#lib/api/problem.ts';
import { describeError } from '#lib/errors.ts';
import { m } from '#lib/paraglide/messages.js';
import { referencedIds, type GroupNode, type RuleModel } from '#lib/rules/model.ts';

export interface SaveErrors {
	/** Messages by key, as `validate` gives them: `name`, `priority`, `triggers`, a block's key. */
	byKey: Record<string, string>;
	/** What belongs to no block. */
	general: string | null;
}

/** The key of the block at `path`, or null. */
export function keyAt(path: string, model: RuleModel): string | null {
	const parts = path.split(/[.[\]]+/).filter(Boolean);
	const [head, ...rest] = parts;
	if (head === 'name' || head === 'priority' || head === 'triggers') return head;
	if (head === 'actions') {
		const index = Number(rest[0]);
		return Number.isInteger(index) ? (model.actions[index]?.key ?? null) : 'actions';
	}
	if (head !== 'conditions') return null;
	let node: GroupNode | RuleModel['conditions']['items'][number] = model.conditions;
	for (let i = 0; i < rest.length; i++) {
		if ((rest[i] === 'all' || rest[i] === 'any') && /^\d+$/.test(rest[i + 1] ?? '')) {
			if (node.kind !== 'group') return null;
			const next: GroupNode['items'][number] | undefined = node.items[Number(rest[i + 1])];
			if (!next) return null;
			node = next;
			i++;
		}
	}
	return node.key;
}

export function saveErrors(error: unknown, model: RuleModel): SaveErrors {
	const result: SaveErrors = { byKey: {}, general: null };
	const detail = error instanceof ApiError ? (error.problem?.detail ?? '') : '';
	if (error instanceof ApiError && error.status === 422 && detail) {
		const rest: string[] = [];
		for (const part of detail.split('; ')) {
			const match = /^([\w.[\]-]+): (.+)$/.exec(part);
			const key = match ? keyAt(match[1], model) : null;
			if (key && match) result.byKey[key] ??= match[2];
			else rest.push(part);
		}
		result.general = rest.length > 0 ? rest.join('; ') : null;
		return result;
	}
	if (error instanceof ApiError && error.status === 404) {
		const missing = /^(\w+) ([0-9a-f-]{36}) not found$/i.exec(detail)?.[2];
		const blocks = missing ? blocksUsing(model, missing) : [];
		for (const key of blocks) result.byKey[key] = m.rule_error_missing();
		if (blocks.length > 0) return result;
	}
	if (error instanceof ApiError && error.status === 403 && /drawer/.test(detail)) {
		const drawer = model.actions.find((action) => action.type === 'set_drawer');
		if (drawer) {
			result.byKey[drawer.key] = describeError(error);
			return result;
		}
	}
	result.general = describeError(error);
	return result;
}

function blocksUsing(model: RuleModel, id: string): string[] {
	const keys: string[] = [];
	const visit = (group: GroupNode) => {
		for (const item of group.items) {
			if (item.kind === 'group') visit(item);
			else if (referencedIds(item).includes(id)) keys.push(item.key);
		}
	};
	visit(model.conditions);
	for (const action of model.actions) {
		if (referencedIds(action).includes(id)) keys.push(action.key);
	}
	return keys;
}
