/* Downloader Dashboard service worker.
 *
 * The dashboard shows live, auth-gated data, so we must NOT cache API
 * responses (they'd go stale and could leak between sessions). We only
 * precache the static app shell so the PWA is installable and the UI paints
 * instantly; everything under /api and the HTML pages always hit the network.
 */
const SHELL_CACHE = "dlweb-shell-v2";
const SHELL = [
  "/static/app.css",
  "/static/app.js",
  "/static/manifest.webmanifest",
  "/static/icons/icon-192.png",
  "/static/icons/icon-512.png",
];

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(SHELL_CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL_CACHE).map((k) => caches.delete(k)))
    ).then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  const url = new URL(req.url);

  // Never cache non-GET, API calls, auth pages, or cross-origin.
  if (
    req.method !== "GET" ||
    url.origin !== self.location.origin ||
    url.pathname.startsWith("/api") ||
    url.pathname === "/login" ||
    url.pathname === "/logout" ||
    url.pathname === "/"
  ) {
    return; // let the browser handle it (network)
  }

  // Static shell: cache-first, fall back to network and populate cache.
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(
      caches.match(req).then(
        (hit) =>
          hit ||
          fetch(req).then((res) => {
            const copy = res.clone();
            caches.open(SHELL_CACHE).then((c) => c.put(req, copy));
            return res;
          }).catch(() => hit)
      )
    );
  }
});
