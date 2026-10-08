import { describe, expect, it } from 'vitest';
import { apiQuery, isFiltered, NO_FILTERS, parseFilters, writeFilters } from './filters.ts';

describe('filters in the URL', () => {
	it('reads and writes all of them', () => {
		const params = new URLSearchParams(
			'contact=c1&type=t1&tag=a&tag=b&drawer=d1&lane=red&lane=bogus&all=1&q=x'
		);
		const filters = parseFilters(params);
		expect(filters).toEqual({
			contact: 'c1',
			type: 't1',
			tags: ['a', 'b'],
			drawer: 'd1',
			lanes: ['red'],
			allUsers: true
		});
		const out = writeFilters(new URLSearchParams('q=x&tag=old'), filters);
		expect(out.getAll('tag')).toEqual(['a', 'b']);
		expect(out.get('q')).toBe('x');
		expect(out.getAll('lane')).toEqual(['red']);
		expect(out.get('all')).toBe('1');
		expect(apiQuery(filters).all_users).toBe(true);
	});

	it('leaves out what is not set', () => {
		expect(writeFilters(new URLSearchParams(), NO_FILTERS).toString()).toBe('');
		expect(apiQuery(NO_FILTERS)).toEqual({
			contact_id: undefined,
			document_type_id: undefined,
			tag_id: undefined,
			drawer_id: undefined,
			lane: undefined,
			all_users: undefined
		});
		expect(isFiltered(NO_FILTERS)).toBe(false);
		expect(isFiltered({ ...NO_FILTERS, tags: ['a'] })).toBe(true);
		expect(isFiltered({ ...NO_FILTERS, allUsers: true })).toBe(false);
	});
});
