// Wall Display Service Worker — cache-first for static assets; pages are left
// to the browser (native network + bfcache) so navigations can never be served
// a blank/undefined response.

const CACHE_NAME = 'wall-display-v2';
// Relative paths resolve against the SW's registered URL ({base_path}/sw.js),
// so they map to the correct ingress-prefixed /static/ URLs.
const STATIC_ASSETS = [
  './static/htmx.min.js',
  './static/icon-192.svg',
  './static/icon-512.svg',
];

self.addEventListener('install', (event) => {
  // Best-effort precache: a single missing asset must never reject install.
  event.waitUntil(
    caches.open(CACHE_NAME)
      .then((cache) => Promise.all(STATIC_ASSETS.map((a) => cache.add(a).catch(() => {}))))
      .catch(() => {})
  );
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== CACHE_NAME).map((k) => caches.delete(k)))
    )
  );
  self.clients.claim();
});

self.addEventListener('fetch', (event) => {
  const url = new URL(event.request.url);

  // Static assets: cache-first (includes() so it matches under the ingress prefix).
  // A cache miss falls through to the network — never resolves to undefined.
  if (url.pathname.includes('/static/')) {
    event.respondWith(
      caches.match(event.request).then((cached) => cached || fetch(event.request))
    );
  }

  // Everything else (pages, API calls): do NOT call respondWith — let the browser
  // handle it natively. This preserves bfcache and removes the old network-first
  // fallback that could resolve to an empty response (the back-button white screen).
});
