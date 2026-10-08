/* eslint-disable @typescript-eslint/ban-ts-comment */
// @ts-nocheck -- a Node build script; the project has no Node type definitions.
import { createReadStream, existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { BASE } from './src/lib/base.ts';

// PDF.js loads decoders (JBIG2, JPEG 2000, colour profiles), the standard fonts and the character
// maps at run time. They are served from the app itself under `pdfjs/`; nothing comes from a CDN.
const FOLDERS = ['wasm', 'standard_fonts', 'cmaps'];

/** @returns {import('vite').Plugin} */
export function pdfjsAssets() {
	const root = dirname(createRequire(import.meta.url).resolve('pdfjs-dist/package.json'));
	const files = () =>
		FOLDERS.flatMap((folder) => readdirSync(join(root, folder)).map((name) => `${folder}/${name}`));
	return {
		name: 'papiq-pdfjs-assets',
		configureServer(server) {
			server.middlewares.use((request, response, next) => {
				const prefix = `${BASE}/pdfjs/`;
				const path = (request.url ?? '').split('?')[0];
				const name = path.startsWith(prefix) ? path.slice(prefix.length) : '';
				if (!files().includes(name)) return next();
				const file = join(root, name);
				if (!existsSync(file) || !statSync(file).isFile()) return next();
				if (name.endsWith('.wasm')) response.setHeader('content-type', 'application/wasm');
				createReadStream(file).pipe(response);
			});
		},
		generateBundle() {
			for (const name of files()) {
				this.emitFile({
					type: 'asset',
					fileName: `pdfjs/${name}`,
					source: readFileSync(join(root, name))
				});
			}
		}
	};
}
