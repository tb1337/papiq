// Smoke test of the built UI against a real API: sign in with password and TOTP, navigate,
// reload, sign out, and the return to the page after signing in again.
import { expect, test, type APIRequestContext } from '@playwright/test';
import { code, step } from './totp.ts';

const ADMIN = process.env.PAPIQ_ADMIN_USERNAME ?? 'admin';
const ADMIN_PASSWORD = process.env.PAPIQ_ADMIN_PASSWORD ?? '';
const USERNAME = `e2e-${Date.now()}`;
const PASSWORD = 'correct horse battery staple';

async function signIn(request: APIRequestContext, username: string, password: string) {
	const response = await request.post('/api/v1/auth/login', { data: { username, password } });
	expect(response.status(), await response.text()).toBe(200);
	return ((await response.json()) as { csrf_token: string }).csrf_token;
}

let secret = '';
let usedStep = 0;

test.beforeAll(async ({ playwright, baseURL }) => {
	test.skip(!ADMIN_PASSWORD, 'PAPIQ_ADMIN_PASSWORD is not set');
	const admin = await playwright.request.newContext({ baseURL });
	const adminToken = await signIn(admin, ADMIN, ADMIN_PASSWORD);
	const created = await admin.post('/api/v1/users', {
		headers: { 'X-CSRF-Token': adminToken },
		data: { username: USERNAME, role: 'user', password: PASSWORD }
	});
	expect(created.status(), await created.text()).toBe(201);
	await admin.dispose();

	const user = await playwright.request.newContext({ baseURL });
	const token = await signIn(user, USERNAME, PASSWORD);
	const setup = await user.post('/api/v1/auth/totp', { headers: { 'X-CSRF-Token': token } });
	expect(setup.ok(), await setup.text()).toBe(true);
	secret = ((await setup.json()) as { secret: string }).secret;
	usedStep = step();
	const confirmed = await user.post('/api/v1/auth/totp/confirm', {
		headers: { 'X-CSRF-Token': token },
		data: { code: await code(secret, usedStep) }
	});
	expect(confirmed.ok(), await confirmed.text()).toBe(true);
	await user.dispose();
});

/**
 * A code the server has not seen yet. It takes each step once, at most one ahead of its clock;
 * when that step is used up, this waits for the next one.
 */
async function freshCode(): Promise<string> {
	while (usedStep >= step() + 1) await new Promise((resolve) => setTimeout(resolve, 1000));
	usedStep = Math.max(step(), usedStep + 1);
	return code(secret, usedStep);
}

test('sign in with TOTP, navigate, reload, sign out', async ({ page }) => {
	await page.goto('/ui/documents');
	await expect(page).toHaveURL(/\/ui\/login\?next=%2Fui%2Fdocuments$/);

	await page.getByLabel('Username').fill(USERNAME);
	await page.getByLabel('Password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.getByLabel('Six-digit code').fill(await freshCode());
	await page.getByRole('button', { name: 'Sign in' }).click();

	await expect(page).toHaveURL(/\/ui\/documents$/);
	const nav = page.getByRole('navigation', { name: 'Main navigation' });
	await expect(nav.getByRole('link', { name: 'Documents' })).toHaveAttribute(
		'aria-current',
		'page'
	);
	await expect(nav.getByRole('link', { name: 'Users' })).toHaveCount(0);

	await nav.getByRole('link', { name: 'Inbox' }).click();
	await expect(page).toHaveURL(/\/ui\/inbox$/);

	await page.reload();
	await expect(page).toHaveURL(/\/ui\/inbox$/);
	await expect(page.getByRole('heading', { name: 'Inbox' })).toBeVisible();

	await page.getByRole('button', { name: `Account ${USERNAME}` }).click();
	await page.getByRole('menuitem', { name: 'Sign out' }).click();
	await expect(page).toHaveURL(/\/ui\/login$/);

	// Signed out: a page leads to the sign-in and back to it afterwards.
	await page.goto('/ui/rules');
	await expect(page).toHaveURL(/\/ui\/login\?next=%2Fui%2Frules$/);
});

test('an ended session leads to the sign-in with the way back', async ({ page, context }) => {
	await page.goto('/ui/login?next=%2Fui%2Fsearch');
	await page.getByLabel('Username').fill(USERNAME);
	await page.getByLabel('Password').fill(PASSWORD);
	await page.getByRole('button', { name: 'Sign in' }).click();
	await page.getByLabel('Six-digit code').fill(await freshCode());
	await page.getByRole('button', { name: 'Sign in' }).click();
	await expect(page).toHaveURL(/\/ui\/search$/);

	// The session ends behind the app's back (another tab, expiry): the next start of the app
	// finds no session and returns to this page after signing in.
	await context.clearCookies();
	await page.reload();
	await expect(page).toHaveURL(/\/ui\/login\?next=%2Fui%2Fsearch$/);
});
