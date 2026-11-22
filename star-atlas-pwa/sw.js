/* Star Atlas service worker.
 *
 * The page is served NETWORK FIRST, cache second. The earlier version did the
 * opposite, which meant a phone that had opened the app once kept serving that
 * copy forever: redeploying changed nothing on the device. Fresh-when-online,
 * working-when-offline is the behaviour you actually want from a single-file app.
 *
 * Icons and the manifest stay cache-first; they never change.
 */
const CACHE = 'star-atlas-v7';
const PAGE  = './index.html';
const CORE  = [
  './', PAGE, './manifest.webmanifest',
  './icon-192.png', './icon-512.png', './icon-maskable-512.png', './apple-touch-icon.png'
];

self.addEventListener('install', e => {
  e.waitUntil((async () => {
    const c = await caches.open(CACHE);
    /* add one at a time: a single missing icon must not abort the install */
    await Promise.all(CORE.map(u => c.add(u).catch(() => {})));
    await self.skipWaiting();
  })());
});

self.addEventListener('activate', e => {
  e.waitUntil((async () => {
    const keys = await caches.keys();
    await Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k)));
    await self.clients.claim();
  })());
});

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;

  const url = new URL(req.url);

  /* the shared gallery must always be live */
  if (url.pathname.includes('/rest/v1/')) return;

  const isPage = req.mode === 'navigate' ||
                 (url.origin === self.location.origin &&
                  (url.pathname.endsWith('/') || url.pathname.endsWith('.html')));

  if (isPage) {
    /* network first, so a redeploy shows up on the next launch */
    e.respondWith((async () => {
      try {
        const fresh = await fetch(req);
        if (fresh && fresh.ok) {
          const copy = fresh.clone();
          caches.open(CACHE).then(c => c.put(PAGE, copy)).catch(() => {});
        }
        return fresh;
      } catch (err) {
        const hit = await caches.match(PAGE);
        return hit || Response.error();
      }
    })());
    return;
  }

  /* everything else: cache first, filled in from the network as needed */
  e.respondWith((async () => {
    const hit = await caches.match(req);
    if (hit) return hit;
    try {
      const res = await fetch(req);
      /* keep the web fonts so the second launch looks right offline */
      if (res && (res.ok || res.type === 'opaque') &&
          (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com')) {
        const copy = res.clone();
        caches.open(CACHE).then(c => c.put(req, copy)).catch(() => {});
      }
      return res;
    } catch (err) {
      return caches.match(PAGE);
    }
  })());
});
