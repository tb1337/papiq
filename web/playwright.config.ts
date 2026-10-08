import { defineConfig, devices } from '@playwright/test';

// The smoke test runs against a running API that serves the built UI (`pnpm build`, then the
// API with PAPIQ_UI_DIR=web/build and PAPIQ_COOKIE_SECURE=false); see README.md. Local only.
export default defineConfig({
	testDir: './tests/e2e',
	// Long enough to wait for a fresh TOTP step.
	timeout: 90_000,
	retries: 0,
	workers: 1,
	reporter: 'list',
	use: {
		baseURL: process.env.PAPIQ_E2E_URL ?? 'http://127.0.0.1:8000',
		// A Chromium of another Playwright version, if `playwright install` is not wanted.
		launchOptions: { executablePath: process.env.PAPIQ_E2E_CHROMIUM },
		trace: 'retain-on-failure'
	},
	projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }]
});
