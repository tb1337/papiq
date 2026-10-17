/**
 * The icons offered first, by areas of life (170; names checked against lucide 1.52): the
 * picker shows them before anything is searched, and the search finds them by the words of
 * the language too (`words.de.ts`, `words.en.ts`). Every other Lucide icon is found by its name.
 */

export type IconArea =
	| 'documents'
	| 'money'
	| 'insurance'
	| 'work'
	| 'health'
	| 'home'
	| 'travel'
	| 'family'
	| 'state'
	| 'technology'
	| 'status';

export const CURATED: readonly { area: IconArea; icons: readonly string[] }[] = [
	{
		area: 'documents',
		icons: [
			'file-text',
			'files',
			'file-check',
			'file-pen-line',
			'file-badge',
			'file-lock',
			'folder',
			'folder-open',
			'archive',
			'inbox',
			'mail',
			'mail-open',
			'send',
			'stamp',
			'signature',
			'paperclip',
			'printer',
			'scroll-text',
			'clipboard-list',
			'clipboard-check',
			'newspaper',
			'notebook-pen'
		]
	},
	{
		area: 'money',
		icons: [
			'banknote',
			'wallet',
			'credit-card',
			'piggy-bank',
			'landmark',
			'coins',
			'euro',
			'receipt',
			'receipt-text',
			'receipt-euro',
			'hand-coins',
			'calculator',
			'percent',
			'chart-line',
			'chart-pie',
			'trending-up',
			'vault'
		]
	},
	{
		area: 'insurance',
		icons: [
			'shield',
			'shield-check',
			'shield-plus',
			'umbrella',
			'scale',
			'gavel',
			'lock',
			'key-round',
			'life-buoy',
			'siren'
		]
	},
	{
		area: 'work',
		icons: [
			'briefcase',
			'briefcase-business',
			'building-complex',
			'factory',
			'handshake',
			'id-card',
			'graduation-cap',
			'school',
			'book-open-check',
			'presentation',
			'laptop',
			'users',
			'calendar-clock'
		]
	},
	{
		area: 'health',
		icons: [
			'heart-pulse',
			'stethoscope',
			'pill',
			'syringe',
			'hospital',
			'activity',
			'cross',
			'brain',
			'eye',
			'glasses',
			'baby',
			'accessibility',
			'bone',
			'ambulance'
		]
	},
	{
		area: 'home',
		icons: [
			'house',
			'building',
			'key',
			'sofa',
			'bed-double',
			'lamp',
			'plug',
			'zap',
			'flame',
			'droplets',
			'thermometer',
			'wifi',
			'router',
			'trash',
			'recycle',
			'wrench',
			'hammer',
			'paint-roller',
			'fence',
			'trees',
			'sprout'
		]
	},
	{
		area: 'travel',
		icons: [
			'car',
			'car-front',
			'bike',
			'bus',
			'train-front',
			'tram-front',
			'plane',
			'ship',
			'fuel',
			'circle-parking',
			'map-pin',
			'luggage',
			'tent',
			'caravan'
		]
	},
	{
		area: 'family',
		icons: [
			'users-round',
			'heart',
			'gift',
			'cake',
			'party-popper',
			'dog',
			'cat',
			'paw-print',
			'camera',
			'music',
			'gamepad-2',
			'dumbbell',
			'ticket',
			'utensils',
			'shopping-cart',
			'shopping-bag',
			'shirt',
			'palette',
			'book',
			'church'
		]
	},
	{
		area: 'state',
		icons: ['flag', 'vote', 'globe', 'book-user', 'badge-check']
	},
	{
		area: 'technology',
		icons: [
			'smartphone',
			'phone',
			'monitor',
			'tv',
			'cloud',
			'server',
			'hard-drive',
			'satellite-dish',
			'signal',
			'package',
			'truck',
			'cpu'
		]
	},
	{
		area: 'status',
		icons: [
			'circle-check',
			'circle-alert',
			'triangle-alert',
			'clock',
			'hourglass',
			'timer',
			'alarm-clock',
			'bell',
			'bell-ring',
			'calendar',
			'calendar-check',
			'bookmark',
			'star',
			'pin',
			'eye-off',
			'list-todo',
			'repeat',
			'reply',
			'forward',
			'thumbs-up',
			'hand',
			'ban'
		]
	}
];

export const CURATED_NAMES: readonly string[] = CURATED.flatMap((group) => group.icons);
