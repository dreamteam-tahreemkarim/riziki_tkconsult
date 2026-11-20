// GET /api/chm?q=<quadkey>  with a Range header  ->  those bytes of one CHMv2 height tile.
// GET /api/chm?meta=<quadkey>                     ->  that tile's photo-date outlines (GeoJSON).
//
// For "Estimate tree height" run on This device (dashboard/height.js). The open
// height map is on AWS S3, which does not let web pages read it directly (no CORS),
// so the page reads it through here. Only height tiles, only byte ranges, at most
// 4 MB per request: this cannot be used to fetch anything else.

const BASE = "https://dataforgood-fb-data.s3.amazonaws.com/forests/v2/global/dinov3_global_chm_v2_ml3/";
const CHM = BASE + "chm/";
const QUADKEY = /^[0-3]{10}$/;
const RANGE = /^bytes=(\d+)-(\d+)$/;
const MAX_BYTES = 4 * 1024 * 1024;

export async function onRequestGet({ request }) {
  const params = new URL(request.url).searchParams;
  const meta = params.get("meta");
  if (meta !== null) {
    if (!QUADKEY.test(meta)) return new Response("meta must be a 10-digit quadkey", { status: 400 });
    const r = await fetch(`${BASE}metadata/${meta}.geojson`, { cf: { cacheTtl: 30 * 86400, cacheEverything: true } });
    if (!r.ok) return new Response(`source answered ${r.status}`, { status: r.status === 404 ? 404 : 502 });
    return new Response(r.body, { headers: { "Content-Type": "application/geo+json", "Cache-Control": "private, max-age=2592000" } });
  }
  const q = params.get("q") || "";
  const m = RANGE.exec(request.headers.get("Range") || "");
  if (!QUADKEY.test(q)) return new Response("q must be a 10-digit quadkey", { status: 400 });
  if (!m || Number(m[2]) < Number(m[1]) || Number(m[2]) - Number(m[1]) + 1 > MAX_BYTES) {
    return new Response("send a Range header of at most 4 MB", { status: 416 });
  }
  const r = await fetch(CHM + q + ".tif", {
    headers: { Range: m[0] },
    cf: { cacheTtl: 30 * 86400, cacheEverything: true },   // the map never changes
  });
  if (r.status !== 206 && r.status !== 200) return new Response(`source answered ${r.status}`, { status: 502 });
  const h = new Headers({ "Content-Type": "application/octet-stream", "Cache-Control": "private, max-age=2592000" });
  for (const k of ["Content-Range", "Content-Length"]) if (r.headers.get(k)) h.set(k, r.headers.get(k));
  return new Response(r.body, { status: r.status, headers: h });
}

export async function onRequest() {
  return new Response("use GET", { status: 405 });
}
