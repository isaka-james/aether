// Aether service worker. Makes the app installable and keeps the shell available offline.
// It only ever touches our own GET requests for the page and static files. It never caches
// the API or the websocket, so logins and live data are always fresh and private.
const CACHE = "aether-shell-v1";
const SHELL = ["/", "/static/app.js", "/static/style.css",
               "/static/manifest.webmanifest", "/static/icon.svg", "/static/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  // Leave everything that isn't a same-origin GET alone: API calls, the websocket, uploads.
  if (e.request.method !== "GET" || url.origin !== location.origin) return;
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/ws")) return;
  // Network first so updates show immediately; fall back to the cached shell when offline.
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const copy = r.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {});
        return r;
      })
      .catch(() => caches.match(e.request).then((m) => m || caches.match("/")))
  );
});
