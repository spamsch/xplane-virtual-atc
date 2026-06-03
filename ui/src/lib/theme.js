import { writable } from 'svelte/store';

// Available UI themes. `key` is written to <html data-theme>; the default
// "midnight" theme is the bare :root and sets no attribute.
export const THEMES = [
  { key: 'midnight',  label: 'MIDNIGHT'  },
  { key: 'technical', label: 'TECHNICAL' },
];

const STORAGE_KEY = 'vatc-theme';
const DEFAULT = 'technical';
// The bare :root holds the midnight palette; every other theme is a
// [data-theme] override layered on top. So midnight — not DEFAULT — is the
// one theme that clears the attribute.
const BASE = 'midnight';

function load() {
  if (typeof localStorage === 'undefined') return DEFAULT;
  const saved = localStorage.getItem(STORAGE_KEY);
  return THEMES.some((t) => t.key === saved) ? saved : DEFAULT;
}

export const theme = writable(load());

// Reflect the active theme onto <html> and persist it. Midnight is the
// default (no attribute) so the base :root variables apply unchanged.
export function applyTheme(key) {
  if (typeof document === 'undefined') return;
  const root = document.documentElement;
  if (key === BASE) root.removeAttribute('data-theme');
  else root.setAttribute('data-theme', key);
  if (typeof localStorage !== 'undefined') localStorage.setItem(STORAGE_KEY, key);
}

export function cycleTheme() {
  theme.update((cur) => {
    const i = THEMES.findIndex((t) => t.key === cur);
    const next = THEMES[(i + 1) % THEMES.length].key;
    applyTheme(next);
    return next;
  });
}
