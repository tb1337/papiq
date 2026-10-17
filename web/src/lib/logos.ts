/** Logos of contacts: whether the server fetches them (asked once, from `GET /pipeline`), and
 * uploading, removing and fetching one. */
import { api } from '#lib/api/client.ts';
import { unwrap } from '#lib/api/call.ts';
import type { Contact } from '#lib/masterdata.svelte.ts';

let fetching: Promise<boolean> | null = null;

/** Whether Papiq fetches logos from websites (`PAPIQ_LOGO_FETCH`); false if it cannot tell. */
export function logoFetching(): Promise<boolean> {
	fetching ??= unwrap(api.GET('/api/v1/pipeline')).then(
		(pipeline) => pipeline.logo_fetch,
		() => {
			fetching = null;
			return false;
		}
	);
	return fetching;
}

/** The contact's domains among its identifiers that count: where its website is. */
export function domainsOf(contact: Pick<Contact, 'identifiers'>): string[] {
	return contact.identifiers
		.filter((item) => item.kind === 'email_domain' && item.source !== 'rejected')
		.map((item) => item.value);
}

export async function uploadLogo(id: string, file: File): Promise<Contact> {
	const form = new FormData();
	form.append('file', file);
	return unwrap(
		api.PUT('/api/v1/contacts/{id}/logo', {
			params: { path: { id } },
			// The generated type wants a string for the file; the form carries the binary.
			body: { file: '' },
			bodySerializer: () => form
		})
	);
}

export async function removeLogo(id: string): Promise<void> {
	await unwrap(api.DELETE('/api/v1/contacts/{id}/logo', { params: { path: { id } } }));
}

export async function fetchLogo(id: string): Promise<void> {
	await unwrap(api.POST('/api/v1/contacts/{id}/logo/fetch', { params: { path: { id } } }));
}
