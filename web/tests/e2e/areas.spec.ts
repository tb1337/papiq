// Smoke test of every area of the built UI against a real API: each page opens without a console
// error (CSP violations included), and one create-and-remove round trip per kind of dialog.
import { expect, test } from '@playwright/test';

const ADMIN = process.env.PAPIQ_ADMIN_USERNAME ?? 'admin';
const ADMIN_PASSWORD = process.env.PAPIQ_ADMIN_PASSWORD ?? '';
const NAME = `e2e-${Date.now()}`;

test.beforeEach(() => {
	test.skip(!ADMIN_PASSWORD, 'PAPIQ_ADMIN_PASSWORD is not set');
});

test('every area opens, and the dialogs create things', async ({ page }) => {
	const problems: string[] = [];
	page.on('console', (message) => {
		// Missing services (search, models) answer 4xx/5xx on purpose; those are not page errors.
		if (message.type() === 'error' && !/status of [45]\d\d/.test(message.text()))
			problems.push(message.text());
	});
	page.on('pageerror', (error) => problems.push(error.message));

	await page.goto('/ui/login');
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password').fill(ADMIN_PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.waitForURL('**/ui/documents');

	const headings: [string, string][] = [
		['/ui/documents', 'Documents'],
		['/ui/inbox', 'Inbox'],
		['/ui/search', 'Search'],
		['/ui/rules', 'Rules'],
		['/ui/drawers', 'Drawers'],
		['/ui/webhooks', 'Webhooks'],
		['/ui/admin/users', 'Users'],
		['/ui/admin/master-data', 'Master data'],
		['/ui/settings', 'Settings']
	];
	for (const [path, heading] of headings) {
		await page.goto(path);
		await expect(page.getByRole('heading', { name: heading, level: 1 })).toBeVisible();
	}

	// Master data: a contact.
	await page.goto('/ui/admin/master-data');
	await page.getByRole('button', { name: 'New contact' }).click();
	await page.getByRole('textbox', { name: 'Name' }).fill(NAME);
	await page.getByRole('button', { name: 'Create' }).click();
	await expect(page.getByText(NAME)).toBeVisible();

	// Drawers: a drawer.
	await page.goto('/ui/drawers');
	await page.getByRole('button', { name: 'New drawer' }).click();
	await page.getByRole('textbox', { name: 'Name' }).fill(NAME);
	await page.getByRole('button', { name: 'Create' }).click();
	await expect(page.getByText(NAME).first()).toBeVisible();

	// Webhooks: create (secret shown once), delete.
	await page.goto('/ui/webhooks');
	await page.getByRole('button', { name: 'New webhook' }).first().click();
	await page.getByRole('textbox', { name: 'Name' }).fill(NAME);
	await page.getByLabel('Target URL').fill('http://127.0.0.1:9/hook');
	await page.getByRole('button', { name: 'Create', exact: true }).click();
	await expect(page.getByTestId('secret')).toContainText('whsec_');
	await page.getByRole('button', { name: 'I have stored it' }).click();
	await page.getByRole('button', { name: `Delete ${NAME}` }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: `Delete` }).click();
	await expect(page.getByText(NAME)).toHaveCount(0);

	// Settings: an API token (shown once), then revoked.
	await page.goto('/ui/settings');
	await page.getByRole('button', { name: 'New token' }).click();
	await page.getByRole('textbox', { name: 'Name' }).fill(NAME);
	await page.getByRole('button', { name: 'Create token' }).click();
	await expect(page.getByTestId('secret')).toContainText('papiq_');
	await page.getByRole('button', { name: 'I have stored it' }).click();
	await page.getByRole('button', { name: `Revoke ${NAME}` }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: 'Revoke' }).click();
	await expect(page.getByText(NAME)).toHaveCount(0);

	expect(problems).toEqual([]);
});
