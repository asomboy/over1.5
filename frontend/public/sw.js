// Service Worker for Soccer Goal Predictor (Offline & PWA Support with Bounded Image Cache)
const SHELL_CACHE = 'sgp-shell-v3';
const IMAGE_CACHE = 'sgp-images-v3';
const VALID_CACHES = [SHELL_CACHE, IMAGE_CACHE];
const MAX_IMAGE_ENTRIES = 120;

const STATIC_ASSETS = ['/favicon.svg', '/manifest.json'];

async function putBoundedImageCache(request, response) {
  const cache = await caches.open(IMAGE_CACHE);
  await cache.put(request, response);
  const keys = await cache.keys();
  if (keys.length > MAX_IMAGE_ENTRIES) {
    const overflow = keys.length - MAX_IMAGE_ENTRIES;
    for (let i = 0; i < overflow; i += 1) {
      await cache.delete(keys[i]);
    }
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SHELL_CACHE).then((cache) => cache.addAll(STATIC_ASSETS)));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((keys) =>
        Promise.all(
          keys.map((key) => {
            if (!VALID_CACHES.includes(key)) {
              return caches.delete(key);
            }
            return Promise.resolve();
          })
        )
      )
      .then(() => self.clients.claim())
  );
});

self.addEventListener('fetch', (event) => {
  if (event.request.method !== 'GET') return;
  const url = new URL(event.request.url);

  if (url.pathname === '/sw.js') {
    event.respondWith(fetch(new Request(event.request, { cache: 'no-store' })));
    return;
  }

  // 1. Network-first strategy for API requests
  if (url.pathname.startsWith('/api/')) {
    event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
    return;
  }

  // 2. Network-first (no-store) for HTML navigations so deploys appear immediately
  if (event.request.mode === 'navigate' || url.pathname === '/' || url.pathname === '/index.html') {
    event.respondWith(
      fetch(new Request(event.request.url, { cache: 'no-store' }))
        .then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put('/index.html', clone));
          }
          return networkResponse;
        })
        .catch(() => caches.match('/index.html'))
    );
    return;
  }

  // 3. Bounded FIFO cache for team/league logos & images
  if (url.pathname.endsWith('.png') || url.pathname.endsWith('.jpg') || url.pathname.endsWith('.webp')) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) return cachedResponse;
        return fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            event.waitUntil(putBoundedImageCache(event.request, clone));
          }
          return networkResponse;
        });
      })
    );
    return;
  }

  // 4. Cache-first for immutable hashed Vite /assets/, fonts, and local SVGs
  if (
    url.pathname.startsWith('/assets/') ||
    url.hostname.includes('fonts.googleapis.com') ||
    url.hostname.includes('fonts.gstatic.com') ||
    url.pathname.endsWith('.svg') ||
    url.pathname.endsWith('.css') ||
    url.pathname.endsWith('.js')
  ) {
    event.respondWith(
      caches.match(event.request).then((cachedResponse) => {
        if (cachedResponse) return cachedResponse;
        return fetch(event.request).then((networkResponse) => {
          if (networkResponse && networkResponse.status === 200) {
            const clone = networkResponse.clone();
            caches.open(SHELL_CACHE).then((cache) => cache.put(event.request, clone));
          }
          return networkResponse;
        });
      })
    );
    return;
  }

  event.respondWith(fetch(event.request).catch(() => caches.match(event.request)));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientsList) => {
      for (const client of clientsList) {
        if ('focus' in client) {
          return client.focus();
        }
      }
      if (self.clients.openWindow) {
        return self.clients.openWindow('/');
      }
      return undefined;
    })
  );
});
