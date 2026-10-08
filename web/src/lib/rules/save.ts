/**
 * Saving a rule switched off (decision E2): the person sees what it would do on the documents
 * there are before switching it on. A new rule is created and switched off at once (the API
 * creates rules switched on; the moment between is accepted, F5a). A changed rule is switched
 * off first and on again if the change fails.
 */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { components } from '#lib/api/schema.ts';
import type { ApiDefinition, Scope } from '#lib/rules/model.ts';

type Rule = components['schemas']['RuleOut'];

function setEnabled(id: string, enabled: boolean): Promise<Rule> {
	return unwrap(api.PATCH('/api/v1/rules/{id}', { params: { path: { id } }, body: { enabled } }));
}

export async function createSwitchedOff(scope: Scope, definition: ApiDefinition): Promise<Rule> {
	const created = await unwrap(api.POST('/api/v1/rules', { body: { scope, ...definition } }));
	return setEnabled(created.id, false);
}

export async function changeSwitchedOff(rule: Rule, definition: ApiDefinition): Promise<Rule> {
	if (rule.enabled) await setEnabled(rule.id, false);
	try {
		return await unwrap(
			api.PUT('/api/v1/rules/{id}', { params: { path: { id: rule.id } }, body: definition })
		);
	} catch (error) {
		if (rule.enabled) await setEnabled(rule.id, true).catch(() => undefined);
		throw error;
	}
}
