// Still Waters — Service Worker
// Caches static assets so the app loads instantly on repeat visits

const CACHE = 'still-waters-v1';
const STATIC = [
  '/',
  '/static/sw.js',
];

// On install: cache the shell
self.addEventListener('install', e => {
  e.waitUntil(
    caches.open(CACHE).then(c => c.addAll(STATIC)).then(() => self.skipWaiting())
  );
});

// On activate: remove old caches
self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys().then(keys =>
      Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

// Fetch strategy:
// - Static assets (fonts, images, CSS, JS from CDNs) → cache-first
// - API routes and stream → network-only
// - HTML page → network-first with cache fallback
self.addEventListener('fetch', e => {
  const url = new URL(e.request.url);

  // Never cache API / SSE
  if (url.pathname.startsWith('/stream') ||
      url.pathname.startsWith('/subscribe') ||
      url.pathname.startsWith('/reading-plan') ||
      url.pathname.startsWith('/verse-card') ||
      url.pathname.startsWith('/search')) {
    return;
  }

  // Cache-first for static assets
  if (url.pathname.startsWith('/static/') ||
      url.hostname === 'fonts.googleapis.com' ||
      url.hostname === 'fonts.gstatic.com' ||
      url.hostname === 'unpkg.com') {
    e.respondWith(
      caches.match(e.request).then(cached => {
        if (cached) return cached;
        return fetch(e.request).then(resp => {
          if (resp.ok) {
            const clone = resp.clone();
            caches.open(CACHE).then(c => c.put(e.request, clone));
          }
          return resp;
        });
      })
    );
    return;
  }

  // Network-first for HTML
  if (e.request.mode === 'navigate') {
    e.respondWith(
      fetch(e.request).catch(() => caches.match('/'))
    );
  }
});
