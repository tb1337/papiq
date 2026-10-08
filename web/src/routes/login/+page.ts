import { redirect } from '@sveltejs/kit';
import { api } from '#lib/api/client.ts';
import { safeNext } from '#lib/navigation.ts';
import { session } from '#lib/session.svelte.ts';

export async function load({ url }) {
	const next = safeNext(url.searchParams.get('next'));
	// Signed in already (another tab, the identity provider): on to where the user wanted to go.
	if (await session.load()) redirect(307, next);
	// The button for the identity provider shows only if one is configured.
	const { data } = await api.GET('/api/v1/auth/oidc');
	return { next, oidc: data?.enabled ? (data.display_name ?? null) : null };
}
