import { describe, expect, it } from 'vitest';
import { home, loginHref, oidcLoginHref, safeNext } from './navigation.ts';

describe('safeNext', () => {
	it.each(['/ui', '/ui/', '/ui/documents', '/ui/search?q=Strom%20Rechnung#top', '/ui/a/b..c'])(
		'keeps the page %s',
		(target) => {
			expect(safeNext(target)).toBe(target);
		}
	);

	it.each([
		null,
		undefined,
		'',
		'/',
		'/api/v1/auth/logout',
		'/uix',
		'https://evil.example/ui/',
		'//evil.example/ui/',
		'/ui/\\evil',
		'/ui/../api/v1/users',
		'/ui/./x',
		'/ui/%2e%2e/api',
		'/ui/%2E%2E/api',
		'/ui/a%2fb',
		'/ui/a%5cb',
		'/ui/a b',
		'/ui/\tx',
		'/ui/\nx',
		'/ui/\u007fx',
		`/ui/${'a'.repeat(2000)}`
	])('refuses %s', (target) => {
		expect(safeNext(target)).toBe(home());
	});

	it('allows dots and encoded slashes after the path', () => {
		expect(safeNext('/ui/search?q=..%2F')).toBe('/ui/search?q=..%2F');
	});
});

describe('loginHref', () => {
	it('carries the page to return to', () => {
		expect(loginHref('/ui/inbox?page=2')).toBe('/ui/login?next=%2Fui%2Finbox%3Fpage%3D2');
	});

	it('leaves out the start page and foreign targets', () => {
		expect(loginHref('/ui/')).toBe('/ui/login');
		expect(loginHref('https://evil.example/')).toBe('/ui/login');
	});
});

describe('oidcLoginHref', () => {
	it('starts at the API with a safe target', () => {
		expect(oidcLoginHref('/ui/rules')).toBe('/api/v1/auth/oidc/login?next=%2Fui%2Frules');
		expect(oidcLoginHref('//evil.example')).toBe('/api/v1/auth/oidc/login?next=%2Fui%2F');
	});
});
