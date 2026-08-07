/* Installable PWA only — do NOT cache app shell or API (avoids stale/broken phone installs). */
self.addEventListener("install", (event) => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.map((k) => caches.delete(k)))).then(() => self.clients.claim())
  );
});

// Network-only: never intercept fetches. Prevents offline cache serving old tunnel/auth builds.
self.addEventListener("fetch", () => {});
