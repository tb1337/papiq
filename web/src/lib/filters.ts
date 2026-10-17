/** The filters of the document view (list and search), as they stand in the page's URL. */
import type { components } from '#lib/api/schema.ts';

export type LaneFilter = components['schemas']['LaneFilter'];
export const LANE_FILTERS: readonly LaneFilter[] = ['green', 'yellow', 'red', 'processing'];

export interface Filters {
	contact: string | null;
	type: string | null;
	/** Each with the tags below it; all of them. */
	tags: string[];
	/** Documents whose contact (type) is in each group or one below it. */
	contactGroups: string[];
	typeGroups: string[];
	/** Documents with one of these topics or one below it (a document has one topic). */
	topics: string[];
	space: string | null;
	lanes: LaneFilter[];
	/** Documents with a value for this field. */
	field: string | null;
}

export const NO_FILTERS: Filters = {
	contact: null,
	type: null,
	tags: [],
	contactGroups: [],
	typeGroups: [],
	topics: [],
	space: null,
	lanes: [],
	field: null
};

/** The filters in `params`. Whom the list covers is no filter but a setting (`scope`). */
export function parseFilters(params: URLSearchParams): Filters {
	return {
		contact: params.get('contact'),
		type: params.get('type'),
		tags: params.getAll('tag'),
		contactGroups: params.getAll('contact_group'),
		typeGroups: params.getAll('type_group'),
		topics: params.getAll('topic'),
		space: params.get('space'),
		lanes: params
			.getAll('lane')
			.filter((lane): lane is LaneFilter => (LANE_FILTERS as readonly string[]).includes(lane)),
		field: params.get('field')
	};
}

/** Write the filters into `params` (other parameters stay). */
export function writeFilters(params: URLSearchParams, filters: Filters): URLSearchParams {
	for (const key of [
		'contact',
		'type',
		'tag',
		'contact_group',
		'type_group',
		'topic',
		'space',
		'lane'
	]) {
		params.delete(key);
	}
	params.delete('field');
	if (filters.contact) params.set('contact', filters.contact);
	if (filters.type) params.set('type', filters.type);
	for (const tag of filters.tags) params.append('tag', tag);
	for (const group of filters.contactGroups) params.append('contact_group', group);
	for (const group of filters.typeGroups) params.append('type_group', group);
	for (const topic of filters.topics) params.append('topic', topic);
	if (filters.space) params.set('space', filters.space);
	for (const lane of filters.lanes) params.append('lane', lane);
	if (filters.field) params.set('field', filters.field);
	return params;
}

/** The query of the API for these filters; `allUsers` from the scope setting. */
export function apiQuery(filters: Filters, allUsers = false) {
	return {
		contact_id: filters.contact ?? undefined,
		document_type_id: filters.type ?? undefined,
		tag_id: filters.tags.length > 0 ? filters.tags : undefined,
		contact_group_id: filters.contactGroups.length > 0 ? filters.contactGroups : undefined,
		document_type_group_id: filters.typeGroups.length > 0 ? filters.typeGroups : undefined,
		topic_id: filters.topics.length > 0 ? filters.topics : undefined,
		space_id: filters.space ?? undefined,
		lane: filters.lanes.length > 0 ? filters.lanes : undefined,
		field_id: filters.field ?? undefined,
		all_users: allUsers || undefined
	};
}

/** Whether a filter narrows the list. */
export function isFiltered(filters: Filters): boolean {
	return Boolean(
		filters.contact ||
		filters.type ||
		filters.space ||
		filters.field ||
		filters.tags.length ||
		filters.contactGroups.length ||
		filters.typeGroups.length ||
		filters.topics.length ||
		filters.lanes.length
	);
}
