/*
 * Offline support. The browser runs this in the background.
 *
 *   /api/ requests    straight to the server, never saved (scan requests, progress, height tiles)
 *   the page itself   network first (so sign-in checks still happen), saved copy if offline
 *   data/ files       network first with a timeout, saved copy if slow or offline
 *   map tiles         saved copy if under a week old, otherwise network; keeps the last 600
 *   everything else   saved copy at once, refreshed in the background for next time
 *
 * Bump VERSION whenever you change any file in FILES, so phones pick it up.
 * (tools/build_site.py stamps it with a hash of those files, so a built copy needs no bump.)
 */
const VERSION = "v4";
const SHELL = "riziki-shell-" + VERSION;
const DATA = "riziki-data";
const TILES = "riziki-tiles";
const MAX_TILES = 600;
const TILE_MAX_AGE = 7 * 24 * 3600 * 1000;

const FILES = [
  "./", "index.html", "style.css", "config.js", "app.js", "height.js", "lock.js", "manifest.webmanifest", "icon.svg", "icon-192.png",
  "vendor/leaflet/leaflet.js", "vendor/leaflet/leaflet.css",
  "vendor/leaflet/images/layers.png", "vendor/leaflet/images/layers-2x.png",
  "vendor/leaflet/images/marker-icon.png", "vendor/leaflet/images/marker-icon-2x.png",
  "vendor/leaflet/images/marker-shadow.png",
  "fonts/cormorant-garamond-latin-600-normal.woff2",
];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(FILES)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys
      .filter((k) => k.startsWith("riziki-") && ![SHELL, DATA, TILES].includes(k))
      .map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  if (req.method !== "GET") return;                     // scan requests go straight to the server
  const url = new URL(req.url);
  if (url.origin === self.location.origin && url.pathname.includes("/api/")) return;
  if (req.mode === "navigate") e.respondWith(networkFirst(req, SHELL, 6000, "index.html"));
  else if (url.pathname.includes("/data/")) e.respondWith(networkFirst(req, DATA, 8000));
  else if (/(^|\.)tile\.openstreetmap\.org$/.test(url.hostname)) e.respondWith(tile(req));
  else if (url.origin === self.location.origin) e.respondWith(staleWhileRevalidate(req));
});

// "?t=123" cache-busters all share one saved copy.
function keyOf(req) {
  const u = new URL(req.url);
  u.search = "";
  return u.href;
}

function withTimeout(promise, ms) {
  return new Promise((resolve, reject) => {
    const t = setTimeout(() => reject(new Error("timeout")), ms);
    promise.then((v) => { clearTimeout(t); resolve(v); }, (err) => { clearTimeout(t); reject(err); });
  });
}

const saveable = (res) => res && res.ok && (res.type === "basic" || res.type === "cors");

async function networkFirst(req, cacheName, ms, fallback) {
  const cache = await caches.open(cacheName);
  try {
    const res = await withTimeout(fetch(req), ms);
    if (saveable(res)) cache.put(keyOf(req), res.clone());
    return res;
  } catch (err) {
    const hit = await cache.match(keyOf(req));
    if (hit) return markSaved(hit);
    const page = fallback && (await caches.match(new URL(fallback, self.registration.scope).href));
    return page || Response.error();
  }
}

// Tells the page "this came from the phone, not the server", so it can say
// "Weak connection" instead of pretending to be online.
async function markSaved(res) {
  const headers = new Headers(res.headers);
  headers.set("X-Riziki-Saved", "1");
  return new Response(await res.blob(), { status: res.status, statusText: res.statusText, headers });
}

async function tile(req) {
  const cache = await caches.open(TILES);
  const hit = await cache.match(req.url);
  const age = hit ? Date.now() - Date.parse(hit.headers.get("date") || 0) : Infinity;
  if (hit && age < TILE_MAX_AGE) return hit;
  try {
    const res = await fetch(req);
    if (saveable(res)) {
      await cache.put(req.url, res.clone());
      trim(cache);
    }
    return res;
  } catch (err) {
    return hit || Response.error();                     // offline: an old tile beats a blank one
  }
}

async function trim(cache) {
  const keys = await cache.keys();
  for (let i = 0; i < keys.length - MAX_TILES; i++) await cache.delete(keys[i]);
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(SHELL);
  const hit = await cache.match(req, { ignoreSearch: true });
  const fresh = fetch(req)
    .then((res) => { if (saveable(res)) cache.put(req, res.clone()); return res; })
    .catch(() => null);
  return hit || (await fresh) || Response.error();
}
