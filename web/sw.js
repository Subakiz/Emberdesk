// Bump SHELL_CACHE when shell files change in a way you need users to get at once.
const SHELL_CACHE = 'bm-shell-v12';
const DATA_CACHE = 'bm-data-v1';
const SHELL = [
  '/', '/index.html', '/app.js', '/styles.css', '/manifest.webmanifest',
  '/icons/icon-192.png', '/icons/icon-512.png', '/icons/apple-touch-icon.png',
  '/vendor/katex/katex.min.js',
];

self.addEventListener('install', e => {
  e.waitUntil(caches.open(SHELL_CACHE).then(c => c.addAll(SHELL.map(u => new Request(u, { cache: 'reload' })))).then(() => self.skipWaiting()));
});

self.addEventListener('activate', e => {
  e.waitUntil(
    caches.keys()
      .then(keys => Promise.all(keys.filter(k => k !== SHELL_CACHE && k !== DATA_CACHE).map(k => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

// Page reads this header to know the answer came from cache (offline banner).
function markStale(res) {
  const headers = new Headers(res.headers);
  headers.set('X-SW-Cache', '1');
  return new Response(res.body, { status: res.status, statusText: res.statusText, headers });
}

async function networkFirst(req) {
  const cache = await caches.open(DATA_CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(req, res.clone());
    return res;
  } catch {
    const hit = await cache.match(req);
    if (hit) return markStale(hit);
    // A network error response still rejects the page's fetch() (so api() calls setOffline), without a rejected respondWith.
    return Response.error();
  }
}

async function cacheFirst(req) {
  const cache = await caches.open(SHELL_CACHE);
  const hit = await cache.match(req, { ignoreSearch: true });
  // Serve cache now, refresh it in the background so the next launch gets updates.
  // no-cache: StaticFiles sends no Cache-Control, so WebKit could otherwise refresh from a stale HTTP cache entry.
  const refresh = fetch(req, { cache: 'no-cache' }).then(res => { if (res.ok) cache.put(req, res.clone()); return res; });
  if (hit) { refresh.catch(() => {}); return hit; }
  return refresh;
}

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // Journal media is never intercepted: a 206 cannot go into Cache Storage, iOS needs native Range handling to play video, and 150 MB files must stay out of the cache.
  if (url.pathname.startsWith('/journal-media/')) return;

  if (url.pathname.startsWith('/api/') || url.pathname.startsWith('/photos/')) {
    e.respondWith(networkFirst(req));
  } else if (req.mode === 'navigate' && (url.pathname === '/' || url.pathname === '/index.html')) {
    e.respondWith(cacheFirst(new Request('/index.html')));
  } else if (SHELL.includes(url.pathname)) {
    e.respondWith(cacheFirst(req));
  }
  // anything else (/calendar.ics, /lock-log) goes straight to the network
});

// Web Push (contract 7.6). Every push must end in a visible notification, or iOS revokes the subscription.
self.addEventListener('push', e => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { /* show a generic notification */ }
  if (!d || typeof d !== 'object') d = {};
  const url = typeof d.url === 'string' && d.url.startsWith('/#/') ? d.url : '/#/journal';
  e.waitUntil(Promise.all([
    self.registration.showNotification(d.title || 'Boredom-Maxxing', {
      body: d.body || '', tag: d.tag || 'bm', data: { url }, icon: '/icons/icon-192.png',
    }),
    self.clients.matchAll({ type: 'window', includeUncontrolled: true })
      .then(list => list.forEach(c => c.postMessage({ type: 'push', tag: d.tag || null }))),
  ]));
});

// WindowClient.navigate is unreliable on iOS: focus the open app and ask the page to move, or open a new window.
self.addEventListener('notificationclick', e => {
  e.notification.close();
  const raw = e.notification.data && e.notification.data.url;
  const url = typeof raw === 'string' && raw.startsWith('/#/') ? raw : '/#/journal';
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
    const client = list.find(c => typeof c.focus === 'function');
    if (!client) return self.clients.openWindow(url);
    return Promise.resolve(client.focus()).catch(() => {}).then(() => client.postMessage({ type: 'open', hash: url.slice(1) }));
  }));
});
