import { paraglideVitePlugin } from '@inlang/paraglide-js';
import adapter from '@sveltejs/adapter-static';
import { sveltekit } from '@sveltejs/kit/vite';
import tailwindcss from '@tailwindcss/vite';
import { svelteTesting } from '@testing-library/svelte/vite';
import { defineConfig } from 'vitest/config';
import { BASE } from './src/lib/base.ts';
import { pdfjsAssets } from './vite-pdfjs.js';

export default defineConfig({
	plugins: [
		tailwindcss(),
		pdfjsAssets(),
		paraglideVitePlugin({
			project: './project.inlang',
			outdir: './src/lib/paraglide',
			// The choice stored in this browser, else the browser's language, else English. The script
			// "paraglide" in package.json (for svelte-check) passes the same.
			strategy: ['localStorage', 'preferredLanguage', 'baseLocale'],
			emitGitIgnore: false
		}),
		sveltekit({
			// A single-page app: every page path falls back to index.html, served by the API.
			adapter: adapter({ fallback: 'index.html' }),
			paths: { base: BASE },
			csp: {
				mode: 'hash',
				directives: {
					'default-src': ['self'],
					'script-src': ['self'],
					// bits-ui, Svelte and the PDF pages set style attributes; no inline <style> elements.
					'style-src': ['self'],
					'style-src-attr': ['unsafe-inline'],
					// PDF.js runs in a worker from this site.
					'worker-src': ['self'],
					'img-src': ['self', 'data:', 'blob:'],
					'font-src': ['self'],
					'connect-src': ['self'],
					'object-src': ['none'],
					'base-uri': ['self'],
					'form-action': ['self']
				}
			}
		}),
		// Only under Vitest: browser builds of Svelte, cleanup after every test.
		svelteTesting()
	],
	server: {
		port: 5173,
		strictPort: true,
		// The API runs beside Vite in the devcontainer; through the proxy, cookies stay same-origin.
		proxy: { '/api': 'http://localhost:8000' }
	},
	test: {
		environment: 'jsdom',
		include: ['src/**/*.test.ts', 'tests/**/*.test.ts'],
		setupFiles: ['./tests/setup.ts']
	}
});
