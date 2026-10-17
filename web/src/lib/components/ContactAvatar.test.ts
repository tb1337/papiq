import { fireEvent, render } from '@testing-library/svelte';
import { describe, expect, it } from 'vitest';
import ContactAvatar from './ContactAvatar.svelte';

describe('ContactAvatar', () => {
	it('shows the initials of a contact without a logo', () => {
		const { container } = render(ContactAvatar, {
			contact: { name: 'Stadtwerke Süd', logo: null }
		});
		expect(container.querySelector('[data-initials]')?.textContent?.trim()).toBe('SS');
		expect(container.querySelector('img')).toBeNull();
	});

	it('shows the logo, and the initials when it cannot be loaded', async () => {
		const url = '/api/v1/contacts/c1/logo?v=1';
		const { container } = render(ContactAvatar, {
			contact: { name: 'Nord Versicherungsgruppe', logo: { url } },
			size: 'lg'
		});
		const image = container.querySelector('img');
		expect(image?.getAttribute('src')).toBe(url);
		expect(image?.getAttribute('alt')).toBe('');
		await fireEvent.error(image!);
		expect(container.querySelector('img')).toBeNull();
		expect(container.querySelector('[data-initials]')?.textContent?.trim()).toBe('NV');
	});
});
