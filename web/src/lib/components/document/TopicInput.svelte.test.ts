import { fireEvent, render, screen, waitFor } from '@testing-library/svelte';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { Tag } from '#lib/masterdata.svelte.ts';
import { m } from '#lib/paraglide/messages.js';
import TopicInput from './TopicInput.svelte';

const topic = (id: string, name: string) =>
	({
		id,
		name,
		path: [name],
		parent_id: null,
		scope: 'topic',
		assignment: 'auto',
		shown: true,
		icon: null,
		color: null,
		created_at: '2026-10-17T12:00:00Z'
	}) as Tag;

const WORK = topic('p1', 'Arbeit');

afterEach(() => vi.unstubAllGlobals());

describe('TopicInput', () => {
	it('offers no topic to create where it is not to (others than admins)', async () => {
		const user = userEvent.setup();
		render(TopicInput, { id: 'topic', topics: [WORK], all: [WORK], placeholder: '—' });
		await user.click(screen.getByRole('button'));
		await user.type(await screen.findByLabelText(m.assignment_search()), 'Zahnzusatz');
		expect(screen.queryByText(m.tag_create_quick({ name: 'Zahnzusatz' }))).toBeNull();
		expect(screen.queryByText(m.tag_create_details())).toBeNull();
	});

	it('sends one creation for a double click and chooses the topic made', async () => {
		let answer: (response: Response) => void = () => {};
		const fetchMock = vi.fn<(request: Request) => Promise<Response>>(
			() => new Promise((resolve) => (answer = resolve))
		);
		vi.stubGlobal('fetch', fetchMock);
		const user = userEvent.setup();
		const props = $state({
			value: '',
			id: 'topic',
			topics: [WORK],
			all: [WORK],
			placeholder: '—',
			creating: true
		});
		render(TopicInput, props);
		await user.click(screen.getByRole('button'));
		await user.type(await screen.findByLabelText(m.picker_search_create()), 'Zahnzusatz');
		const offer = screen.getByText(m.tag_create_quick({ name: 'Zahnzusatz' }));
		await fireEvent.click(offer);
		await fireEvent.click(offer);
		expect(fetchMock).toHaveBeenCalledTimes(1);
		answer(
			new Response(JSON.stringify(topic('p9', 'Zahnzusatz')), {
				status: 201,
				headers: { 'content-type': 'application/json' }
			})
		);
		await waitFor(() => expect(props.value).toBe('p9'));
		expect(fetchMock).toHaveBeenCalledTimes(1);
	});
});
