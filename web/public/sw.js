// Service worker minimal : l'app s'ouvre même hors ligne (coquille en cache), l'API n'est jamais mise en cache.
// The shell opens at once from the cache, as an installed app does, and is refreshed behind it; when the refreshed
// shell differs (a new version was deployed), the open pages are told, and the next pull to refresh reloads them.
const CACHE = "kb-shell-v3";

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(["/", "/manifest.webmanifest"])).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  );
});

self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);
  if (event.request.method !== "GET" || url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api/") || url.pathname.startsWith("/mcp")) return;

  if (url.pathname.startsWith("/assets/")) {
    // fichiers versionnés par Vite : cache d'abord
    event.respondWith(
      caches.match(event.request).then((hit) => hit || fetch(event.request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(event.request, copy));
        return res;
      })),
    );
    return;
  }

  if (event.request.mode === "navigate") {
    // pages: the cached shell at once, the network behind it (or first, the very first time)
    event.respondWith(caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match("/");
      const fresh = fetch(event.request).then(async (res) => {
        if (!res.ok || res.type !== "basic") return res;
        const text = await res.clone().text();
        const before = cached ? await cached.clone().text() : null;
        await cache.put("/", res.clone());
        if (before !== null && before !== text) {
          const pages = await self.clients.matchAll({ type: "window" });
          pages.forEach((page) => page.postMessage({ type: "kb:update" }));
        }
        return res;
      });
      if (!cached) return fresh;
      event.waitUntil(fresh.catch(() => {}));
      return cached;
    }));
  }
});
