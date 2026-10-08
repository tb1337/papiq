// Sets the colour scheme before the app starts, so that a dark page does not flash white.
// The key and values are those of mode-watcher, which takes over once the app runs.
(function () {
	var mode = null;
	try {
		mode = localStorage.getItem('mode-watcher-mode');
	} catch {
		// Storage can be blocked; follow the system then.
	}
	var dark =
		mode === 'dark' ||
		(mode !== 'light' && window.matchMedia('(prefers-color-scheme: dark)').matches);
	document.documentElement.classList.toggle('dark', dark);
	document.documentElement.style.colorScheme = dark ? 'dark' : 'light';
})();
