import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import { userEvent } from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Tag } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import { session } from '#lib/session.svelte.ts';
import { SEPARATOR } from '#lib/tags.ts';
import TagsInput from './TagsInput.svelte';

function tag(id: string, path: string[], parent: string | null, scope = 'document'): Tag {
	return {
		id,
		name: path[path.length - 1],
		path,
		parent_id: parent,
		scope: scope as Tag['scope'],
		assignment: 'auto',
		shown: true,
		icon: null,
		color: null,
		created_at: '2026-10-16T12:00:00Z'
	};
}

const ALL = [
	tag('t1', ['Insurance'], null),
	tag('t2', ['Insurance', 'Health'], 't1'),
	tag('t3', ['Paid'], null),
	tag('g1', ['Insurer'], null, 'contact')
];

describe('TagsInput', () => {
	it('shows the leaf with its path as tooltip and removes a tag', async () => {
		const props = $state({ tags: ['t2'], all: ALL, id: 'tags' });
		render(TagsInput, props);
		const chip = screen.getByRole('button', { name: `Insurance${SEPARATOR}Health` });
		expect(chip.textContent?.trim()).toBe('Health');
		expect(chip.closest('[title]')?.getAttribute('title')).toBe(`Insurance${SEPARATOR}Health`);
		await fireEvent.click(
			screen.getByRole('button', {
				name: m.filter_tag_remove({ name: `Insurance${SEPARATOR}Health` })
			})
		);
		expect(props.tags).toEqual([]);
	});

	it('finds a tag by the name of its parent, offers no group, and keeps the child', async () => {
		const user = userEvent.setup();
		const props = $state({ tags: ['t1'], all: ALL, id: 'tags' });
		render(TagsInput, props);
		await user.click(screen.getByRole('button', { name: m.field_tag_add() }));
		const search = await screen.findByLabelText(m.assignment_search());
		expect(screen.queryByText('Insurer')).toBeNull();
		await user.type(search, 'insur');
		expect(screen.getByText('Health')).toBeTruthy();
		expect(screen.queryByText('Paid')).toBeNull();
		await user.click(screen.getByText('Health'));
		expect(props.tags).toEqual(['t2']); // the parent gives way to the child
	});

	it('does not offer the tags above one set', async () => {
		const user = userEvent.setup();
		render(TagsInput, { tags: ['t2'], all: ALL, id: 'tags' });
		await user.click(screen.getByRole('button', { name: m.field_tag_add() }));
		await screen.findByLabelText(m.assignment_search());
		expect(screen.getByText('Paid')).toBeTruthy();
		expect(screen.queryByRole('option', { name: 'Insurance' })).toBeNull();
	});

	it('keeps the id for the label when there is nothing to add', () => {
		const { container } = render(TagsInput, { tags: [], all: ALL, id: 'tags', disabled: true });
		expect(container.querySelector('#tags')?.getAttribute('aria-labelledby')).toBe('tags-label');
	});

	describe('creating', () => {
		afterEach(() => {
			session.clear();
			vi.unstubAllGlobals();
		});

		function signIn(role: 'admin' | 'user') {
			session.start({
				user: { id: 'u1', username: 'tobi', role },
				method: 'password',
				expires_at: '2026-10-17T23:00:00Z',
				csrf_token: 'token'
			});
		}

		it('lets an admin create a document tag named as searched, set at once', async () => {
			signIn('admin');
			const fetchMock = vi.fn(async (request: Request) => {
				const body = await request.clone().json();
				return new Response(JSON.stringify(tag('t9', [body.name], null)), {
					status: 201,
					headers: { 'content-type': 'application/json' }
				});
			});
			vi.stubGlobal('fetch', fetchMock);
			const user = userEvent.setup();
			const props = $state({ tags: ['t3'], all: ALL, id: 'tags' });
			render(TagsInput, props);
			await user.click(screen.getByRole('button', { name: m.field_tag_add() }));
			const search = await screen.findByLabelText(m.picker_search_create());
			// A group of the same name is no document tag: creating is offered.
			await user.type(search, 'Insurer');
			await user.click(screen.getByText(m.tag_create_quick({ name: 'Insurer' })));
			await waitFor(() => expect(props.tags).toEqual(['t3', 't9']));
			expect(await fetchMock.mock.calls[0][0].json()).toEqual({
				name: 'Insurer',
				scope: 'document'
			});
			// Shown by its name before the lookup has it.
			expect(
				screen.getByRole('button', { name: m.filter_tag_remove({ name: 'Insurer' }) })
			).toBeTruthy();
		});

		it('sends one creation for a double click', async () => {
			signIn('admin');
			let answer: (response: Response) => void = () => {};
			const fetchMock = vi.fn<(request: Request) => Promise<Response>>(
				() => new Promise((resolve) => (answer = resolve))
			);
			vi.stubGlobal('fetch', fetchMock);
			const user = userEvent.setup();
			const props = $state({ tags: [] as string[], all: ALL, id: 'tags' });
			render(TagsInput, props);
			await user.click(screen.getByRole('button', { name: m.field_tag_add() }));
			await user.type(await screen.findByLabelText(m.picker_search_create()), 'Makler');
			const offer = screen.getByText(m.tag_create_quick({ name: 'Makler' }));
			await fireEvent.click(offer);
			await fireEvent.click(offer);
			await fireEvent.keyDown(offer, { key: 'Enter' });
			expect(fetchMock).toHaveBeenCalledTimes(1);
			answer(
				new Response(JSON.stringify(tag('t9', ['Makler'], null)), {
					status: 201,
					headers: { 'content-type': 'application/json' }
				})
			);
			await waitFor(() => expect(props.tags).toEqual(['t9']));
			expect(fetchMock).toHaveBeenCalledTimes(1);
		});

		it('offers nothing to create to others', async () => {
			signIn('user');
			const user = userEvent.setup();
			render(TagsInput, { tags: [], all: ALL, id: 'tags' });
			await user.click(screen.getByRole('button', { name: m.field_tag_add() }));
			await user.type(await screen.findByLabelText(m.assignment_search()), 'Makler');
			expect(screen.queryByText(m.tag_create_quick({ name: 'Makler' }))).toBeNull();
			expect(screen.queryByText(m.tag_create_details())).toBeNull();
		});

		it('offers nothing to create for a name a document tag has', async () => {
			signIn('admin');
			const user = userEvent.setup();
			render(TagsInput, { tags: ['t3'], all: ALL, id: 'tags' });
			await user.click(screen.getByRole('button', { name: m.field_tag_add() }));
			await user.type(await screen.findByLabelText(m.picker_search_create()), 'PAID ');
			expect(screen.queryByText(m.tag_create_quick({ name: 'PAID' }))).toBeNull();
		});
	});
});
