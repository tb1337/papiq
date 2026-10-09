import { describe, expect, it } from 'vitest';
import schema from '../../../openapi.json';
import source from './TokensCard.svelte?raw';

describe('TokensCard', () => {
	it('limits the token name as the API does', () => {
		const limit = (
			schema as {
				components: { schemas: { TokenCreate: { properties: { name: { maxLength: number } } } } };
			}
		).components.schemas.TokenCreate.properties.name.maxLength;
		expect(source).toContain(`maxlength={${limit}}`);
	});
});
