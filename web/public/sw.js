// Service worker minimal : l'app s'ouvre même hors ligne (coquille en cache), l'API n'est jamais mise en cache.
// The shell opens at once from the cache, as an installed app does, and is refreshed behind it. A shell is only kept
// once every file it loads (/assets/…, versioned by Vite) is in the cache too, so a cached shell always starts, even
// after the server has moved on to newer files. When the refreshed shell differs (a new version was deployed), the open
// pages are told, and the next pull to refresh reloads them.
const CACHE = "kb-shell-v4";

/** The versioned files a shell loads (its scripts and styles; fonts are found by the styles, and fall back if gone). */
const assetsOf = (html) => [...new Set([...html.matchAll(/(?:src|href)="(\/assets\/[^"]+)"/g)].map((m) => m[1]))];

/** Keeps `res` as the shell once its files are cached, and drops the files no shell needs any more (but fonts).
 *  Returns false when a file couldn't be had: the shell before it stays. */
async function keepShell(cache, res) {
  const html = await res.clone().text();
  const wanted = assetsOf(html);
  try {
    await Promise.all(wanted.map(async (path) => {
      if (await cache.match(path)) return;
      const file = await fetch(path);
      if (!file.ok) throw new Error(`${path}: ${file.status}`);
      await cache.put(path, file);
    }));
  } catch {
    return false;
  }
  await cache.put("/", res);
  for (const req of await cache.keys()) {
    const path = new URL(req.url).pathname;
    if (path.startsWith("/assets/") && !wanted.includes(path) && !/\.(woff2?|ttf|otf)$/.test(path)) await cache.delete(req);
  }
  return true;
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then(async (cache) => {
    await cache.add("/manifest.webmanifest").catch(() => {});
    const res = await fetch("/");
    if (res.ok) await keepShell(cache, res);
  }).then(() => self.skipWaiting()));
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
    // fichiers versionnés par Vite : cache d'abord (an answer that isn't the file is never kept)
    event.respondWith(
      caches.match(event.request).then((hit) => hit || fetch(event.request).then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(event.request, copy));
        }
        return res;
      })),
    );
    return;
  }

  if (event.request.mode === "navigate") {
    // pages: the cached shell at once, the network behind it (or first, the very first time)
    event.respondWith(caches.open(CACHE).then(async (cache) => {
      const cached = await cache.match("/");
      const before = cached ? await cached.clone().text() : null;      // read now: `cached` is about to be shown
      const fresh = fetch(event.request).then(async (res) => {
        if (!res.ok || res.type !== "basic") return res;
        const text = await res.clone().text();
        if (text !== before && (await keepShell(cache, res.clone())) && before !== null) {
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
