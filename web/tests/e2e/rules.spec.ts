// The rule builder against a real API: a nested rule is built, checked before saving, saved
// switched off, dry-run on an existing document, switched on and applied, changed, read in an
// earlier version, opened and saved again unchanged (no new version), and deleted.
import { expect, test, type APIRequestContext, type Page } from '@playwright/test';

const ADMIN = process.env.PAPIQ_ADMIN_USERNAME ?? 'admin';
const ADMIN_PASSWORD = process.env.PAPIQ_ADMIN_PASSWORD ?? '';
const NAME = `e2e-rule-${Date.now()}`;
const PDF = `%PDF-1.4
1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj 2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj
3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj
trailer<</Root 1 0 R>>
%%EOF
% ${NAME}`;

let api: APIRequestContext;
let csrf = '';
const created: { contact: string; tag: string; document: string } = {
	contact: '',
	tag: '',
	document: ''
};

async function call(method: 'post' | 'patch' | 'delete', path: string, data?: object) {
	const response = await api[method](`/api/v1${path}`, {
		headers: { 'X-CSRF-Token': csrf },
		data
	});
	expect(response.ok(), await response.text()).toBe(true);
	return response.status() === 204 ? null : await response.json();
}

test.beforeAll(async ({ playwright, baseURL }) => {
	test.skip(!ADMIN_PASSWORD, 'PAPIQ_ADMIN_PASSWORD is not set');
	// Without OCR programs the document turns red only after the step's retries.
	test.setTimeout(240_000);
	api = await playwright.request.newContext({ baseURL });
	const login = await api.post('/api/v1/auth/login', {
		data: { username: ADMIN, password: ADMIN_PASSWORD }
	});
	expect(login.status(), await login.text()).toBe(200);
	csrf = ((await login.json()) as { csrf_token: string }).csrf_token;
	created.contact = (await call('post', '/contacts', { name: `${NAME} contact` })).id;
	created.tag = (await call('post', '/tags', { name: `${NAME} tag` })).id;
	const upload = await api.post('/api/v1/documents', {
		headers: { 'X-CSRF-Token': csrf },
		multipart: {
			file: { name: `${NAME}.pdf`, mimeType: 'application/pdf', buffer: Buffer.from(PDF) }
		}
	});
	expect(upload.status(), await upload.text()).toBe(202);
	created.document = (await upload.json()).id;
	// Out of processing (green, yellow or red, depending on the services), then the contact.
	await expect
		.poll(
			async () => (await (await api.get(`/api/v1/documents/${created.document}`)).json()).lane,
			{ timeout: 200_000 }
		)
		.not.toBeNull();
	await call('patch', `/documents/${created.document}`, { contact_id: created.contact });
});

test.afterAll(async () => {
	if (!api) return;
	if (created.document) await call('delete', `/documents/${created.document}`).catch(() => {});
	if (created.tag) await call('delete', `/tags/${created.tag}`).catch(() => {});
	if (created.contact) await call('delete', `/contacts/${created.contact}`).catch(() => {});
	await api.dispose();
});

async function signIn(page: Page) {
	await page.goto('/ui/login');
	await page.getByLabel('Username').fill(ADMIN);
	await page.getByLabel('Password').fill(ADMIN_PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.waitForURL('**/ui/documents');
}

test('a rule is built, dry-run, applied, changed and deleted', async ({ page }) => {
	const problems: string[] = [];
	page.on('console', (message) => {
		if (message.type() === 'error' && !/status of [45]\d\d/.test(message.text()))
			problems.push(message.text());
	});
	page.on('pageerror', (error) => problems.push(error.message));
	await signIn(page);

	await page.goto('/ui/rules');
	await page.getByRole('link', { name: 'New rule' }).click();
	await expect(page.getByRole('heading', { name: 'New rule', level: 1 })).toBeVisible();

	// Checked before saving: no name, no value, no action.
	await page.getByRole('button', { name: 'Save and see what it does' }).click();
	await expect(page.getByText('Give the rule a name.')).toBeVisible();
	await expect(page.getByText('Add at least one action.')).toBeVisible();

	await page.getByRole('textbox', { name: 'Name' }).fill(NAME);
	const root = page.getByRole('group', { name: 'Conditions' });
	// All of: channel is one of web, and a group (any): contact is ours, or text contains NAME.
	await root.getByLabel('Field').first().selectOption('channel');
	await root.getByLabel('Comparison').first().selectOption('in');
	await root.getByRole('group', { name: 'Value' }).getByLabel('Web UI').check();
	await root.getByRole('button', { name: 'Add group' }).last().click();
	const group = root.getByRole('group', { name: 'Group' });
	await group.getByLabel('Field').selectOption('contact');
	await group.getByLabel('Value').selectOption({ label: `${NAME} contact` });
	await group.getByRole('button', { name: 'Add condition' }).click();
	await group.getByLabel('Field').last().selectOption('text');
	await group.getByLabel('Value').last().fill(NAME);
	await page.getByLabel('Add action').selectOption('add_tags');
	await page.getByRole('group', { name: 'Add tags' }).getByLabel(`${NAME} tag`).check();
	let previews = 0;
	page.on('request', (request) => {
		if (request.url().endsWith('/apply/preview')) previews++;
	});
	await page.getByRole('button', { name: 'Save and see what it does' }).click();

	// Saved switched off; the dry run shows the document, chosen.
	await page.waitForURL(/\/ui\/rules\/[^/]+\/apply\?saved=1$/);
	const ruleUrl = page.url().replace(/\/apply\?saved=1$/, '');
	await expect(page.getByRole('link', { name: `${NAME}` }).first()).toBeVisible();
	await expect(page.getByRole('checkbox', { name: `Apply to ${NAME}` })).toBeChecked();
	expect(previews).toBe(1);
	await page.getByRole('button', { name: /^Switch on and apply to \d+ documents$/ }).click();
	await expect(page.getByText(/^Done: 1 changed/)).toBeVisible({ timeout: 30_000 });
	const document = await (await api.get(`/api/v1/documents/${created.document}`)).json();
	expect(document.tag_ids).toContain(created.tag);

	// Switched on, version 1; a change makes version 2 and switches it off until decided.
	await page.goto(ruleUrl);
	await expect(page.getByRole('switch', { name: `Switch off ${NAME}` })).toBeChecked();
	await page.getByRole('link', { name: 'Edit' }).click();
	await page.getByRole('textbox', { name: 'Name' }).fill(`${NAME} v2`);
	await page.getByRole('button', { name: 'Save and see what it does' }).click();
	await page.waitForURL(/\/apply\?saved=1$/);
	await page.getByRole('link', { name: 'Leave off' }).click();
	await expect(page.getByRole('switch', { name: `Switch on ${NAME} v2` })).not.toBeChecked();
	await expect(page.getByRole('link', { name: 'Version 2' })).toBeVisible();

	// A refused switch stays as the rule is.
	await page.route('**/api/v1/rules/*', (route) =>
		route.request().method() === 'PATCH'
			? route.fulfill({
					status: 404,
					contentType: 'application/problem+json',
					body: JSON.stringify({ title: 'Not Found', status: 404, detail: 'tag gone' })
				})
			: route.fallback()
	);
	await page.getByRole('switch', { name: `Switch on ${NAME} v2` }).click();
	await expect(page.getByText('tag gone')).toBeVisible();
	await expect(page.getByRole('switch', { name: `Switch on ${NAME} v2` })).not.toBeChecked();
	await page.unroute('**/api/v1/rules/*');

	// Version 1 reads as it was.
	await page.getByRole('link', { name: 'Version 1' }).click();
	await expect(page.getByRole('heading', { name: `${NAME}, version 1` })).toBeVisible();
	await expect(page.getByText(`Contact is ${NAME} contact`)).toBeVisible();

	// The builder gives an unchanged rule back unchanged: saving makes no version.
	await page.goto(`${ruleUrl}/edit`);
	await page.getByRole('button', { name: 'Save and see what it does' }).click();
	await page.waitForURL(ruleUrl);
	await expect(page.getByRole('link', { name: 'Version 3' })).toHaveCount(0);

	// Deleted: gone from the list.
	await page.getByRole('button', { name: `Delete ${NAME} v2` }).click();
	await page.getByRole('alertdialog').getByRole('button', { name: 'Delete' }).click();
	await page.waitForURL('**/ui/rules');
	await expect(page.getByText(`${NAME} v2`)).toHaveCount(0);

	expect(problems).toEqual([]);
});
