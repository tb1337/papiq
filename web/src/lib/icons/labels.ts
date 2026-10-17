/** What a person reads of icons: the names of the areas and an icon's label in the language. */
import type { IconArea } from '#lib/icons/curated.ts';
import { WORDS as DE } from '#lib/icons/words.de.ts';
import { WORDS as EN } from '#lib/icons/words.en.ts';
import { getLocale } from '#lib/i18n.ts';
import { m } from '#lib/paraglide/messages.js';

export const AREA_LABELS: Record<IconArea, () => string> = {
	documents: m.icon_area_documents,
	money: m.icon_area_money,
	insurance: m.icon_area_insurance,
	work: m.icon_area_work,
	health: m.icon_area_health,
	home: m.icon_area_home,
	travel: m.icon_area_travel,
	family: m.icon_area_family,
	state: m.icon_area_state,
	technology: m.icon_area_technology,
	status: m.icon_area_status
};

/** The first search word of an icon offered first, in the language; else its Lucide name. */
export function iconLabel(name: string): string {
	const words = (getLocale() === 'de' ? DE : EN)[name];
	return words ? `${words.split(' ')[0]} (${name})` : name;
}
