import type { Component } from 'svelte';
import Archive from '@lucide/svelte/icons/archive';
import FileText from '@lucide/svelte/icons/file-text';
import Inbox from '@lucide/svelte/icons/inbox';
import Search from '@lucide/svelte/icons/search';
import Settings from '@lucide/svelte/icons/settings';
import Tags from '@lucide/svelte/icons/tags';
import Users from '@lucide/svelte/icons/users';
import Webhook from '@lucide/svelte/icons/webhook';
import Workflow from '@lucide/svelte/icons/workflow';
import { BASE } from '#lib/base.ts';
import { m } from '#lib/paraglide/messages.js';

/** An area of the app. `admin` areas are hidden for other users; the API checks the rights. */
export interface Section {
	path: string;
	label: () => string;
	icon: Component;
	admin: boolean;
}

export const SECTIONS: readonly Section[] = [
	{ path: '/documents', label: m.nav_documents, icon: FileText, admin: false },
	{ path: '/inbox', label: m.nav_inbox, icon: Inbox, admin: false },
	{ path: '/search', label: m.nav_search, icon: Search, admin: false },
	{ path: '/drawers', label: m.nav_drawers, icon: Archive, admin: false },
	{ path: '/rules', label: m.nav_rules, icon: Workflow, admin: false },
	{ path: '/webhooks', label: m.nav_webhooks, icon: Webhook, admin: false },
	{ path: '/admin/users', label: m.nav_users, icon: Users, admin: true },
	{ path: '/admin/master-data', label: m.nav_master_data, icon: Tags, admin: true }
];

export const SETTINGS: Section = {
	path: '/settings',
	label: m.nav_settings,
	icon: Settings,
	admin: false
};

/** The areas this user sees: all for admins, the others without the admin areas. */
export function visibleSections(isAdmin: boolean): Section[] {
	return SECTIONS.filter((section) => isAdmin || !section.admin);
}

export function href(section: Section): string {
	return `${BASE}${section.path}`;
}

/** Whether `pathname` lies in the section. */
export function isCurrent(section: Section, pathname: string): boolean {
	const target = href(section);
	return pathname === target || pathname.startsWith(`${target}/`);
}
