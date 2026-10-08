/** The filters of the document list and of the search, as they stand in the page's URL. */
import type { components } from '#lib/api/schema.ts';

export type LaneFilter = components['schemas']['LaneFilter'];
export const LANE_FILTERS: readonly LaneFilter[] = ['green', 'yellow', 'red', 'processing'];

export interface Filters {
	contact: string | null;
	type: string | null;
	tags: string[];
	drawer: string | null;
	lanes: LaneFilter[];
	/** Admins: the documents of all users, not only their reach. */
	allUsers: boolean;
}

export const NO_FILTERS: Filters = {
	contact: null,
	type: null,
	tags: [],
	drawer: null,
	lanes: [],
	allUsers: false
};

/** The filters in `params`; `all=1` counts for admins only (`admin`), as only they may ask. */
export function parseFilters(params: URLSearchParams, admin = false): Filters {
	return {
		contact: params.get('contact'),
		type: params.get('type'),
		tags: params.getAll('tag'),
		drawer: params.get('drawer'),
		lanes: params
			.getAll('lane')
			.filter((lane): lane is LaneFilter => (LANE_FILTERS as readonly string[]).includes(lane)),
		allUsers: admin && params.get('all') === '1'
	};
}

/** Write the filters into `params` (other parameters stay). */
export function writeFilters(params: URLSearchParams, filters: Filters): URLSearchParams {
	for (const key of ['contact', 'type', 'tag', 'drawer', 'lane', 'all']) params.delete(key);
	if (filters.contact) params.set('contact', filters.contact);
	if (filters.type) params.set('type', filters.type);
	for (const tag of filters.tags) params.append('tag', tag);
	if (filters.drawer) params.set('drawer', filters.drawer);
	for (const lane of filters.lanes) params.append('lane', lane);
	if (filters.allUsers) params.set('all', '1');
	return params;
}

/** The query of the API for these filters. */
export function apiQuery(filters: Filters) {
	return {
		contact_id: filters.contact ?? undefined,
		document_type_id: filters.type ?? undefined,
		tag_id: filters.tags.length > 0 ? filters.tags : undefined,
		drawer_id: filters.drawer ?? undefined,
		lane: filters.lanes.length > 0 ? filters.lanes : undefined,
		all_users: filters.allUsers || undefined
	};
}

/** Whether a filter narrows the list; the scope of all users is none. */
export function isFiltered(filters: Filters): boolean {
	return Boolean(
		filters.contact || filters.type || filters.drawer || filters.tags.length || filters.lanes.length
	);
}
