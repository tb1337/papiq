import { redirect } from '@sveltejs/kit';
import { BASE } from '#lib/base.ts';

// The start page is the document list.
export function load() {
	redirect(307, `${BASE}/documents`);
}
