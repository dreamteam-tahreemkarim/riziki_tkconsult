/*
 * Tree height on this device: the browser twin of mapping/height.py.
 *
 * Reads the open Meta/WRI canopy height map (1.2 m GeoTIFF tiles) through
 * /api/chm, because the source does not let web pages read it directly. Every
 * pixel is counted into the dashboard cell its centre falls in, and cover /
 * median height / top height per cell are read off the counts, exactly as the
 * Python side does, so both give the same numbers.
 *
 * Pause and resume: the work goes strip by strip (512 pixel rows of one tile).
 * After each strip the counts are saved in IndexedDB, so a pause, a lost
 * connection, a closed tab or a reload only costs the strip in progress.
 */
(function () {
  "use strict";

  const ZOOM = 10, TILE_PX = 32768, BLOCK = 512, R = 6378137;
  const BINS = 65, NODATA = 255, TREE_M = 1;        // heights 0-63 m, bin 64 = no data
  const RAD = Math.PI / 180, DEG = 180 / Math.PI;
  const PARALLEL = 4;                               // tiles downloaded at once

  // ------------------------------------------------- geometry (as in height.py)
  const tileXY = (lat, lon) => {
    const n = 2 ** ZOOM;
    return [Math.floor((lon + 180) / 360 * n),
            Math.floor((1 - Math.asinh(Math.tan(lat * RAD)) / Math.PI) / 2 * n)];
  };
  const quadkey = (x, y) => {
    let k = "";
    for (let i = ZOOM - 1; i >= 0; i--) k += ((x >> i) & 1) + 2 * ((y >> i) & 1);
    return k;
  };
  const tileOrigin = (x, y) => {
    const size = 2 * Math.PI * R / 2 ** ZOOM;
    return [-Math.PI * R + x * size, Math.PI * R - y * size, size / TILE_PX];
  };
  const mercX = (lon) => lon * RAD * R;
  const mercY = (lat) => Math.log(Math.tan(Math.PI / 4 + lat * RAD / 2)) * R;

  function tilesFor(b) {
    const [x0, y0] = tileXY(b.n - 1e-9, b.w + 1e-9), [x1, y1] = tileXY(b.s + 1e-9, b.e - 1e-9);
    const out = [];
    for (let y = y0; y <= y1; y++) for (let x = x0; x <= x1; x++) out.push([x, y]);
    return out;
  }

  function pixelWindow(b, x0m, y0m, res) {
    return [Math.max(0, Math.floor((mercX(b.w) - x0m) / res)), Math.min(TILE_PX, Math.ceil((mercX(b.e) - x0m) / res)),
            Math.max(0, Math.floor((y0m - mercY(b.n)) / res)), Math.min(TILE_PX, Math.ceil((y0m - mercY(b.s)) / res))];
  }

  // Work units in reading order: [tile x, tile y, strip].
  function units(b) {
    const out = [];
    for (const [x, y] of tilesFor(b)) {
      const [c0, c1, r0, r1] = pixelWindow(b, ...tileOrigin(x, y));
      if (c1 > c0 && r1 > r0) for (let k = Math.floor(r0 / BLOCK); k <= Math.floor((r1 - 1) / BLOCK); k++) out.push([x, y, k]);
    }
    return out;
  }

  // ------------------------------------------------------------ reading tiles
  async function getRange(url, a, b, signal) {
    const r = await fetch(url, { headers: { Range: `bytes=${a}-${b}` }, signal, credentials: "same-origin", cache: "no-store" });
    if (r.status !== 206 && r.status !== 200) throw new Error(`height map answered ${r.status}`);
    const buf = new Uint8Array(await r.arrayBuffer());
    return r.status === 200 ? buf.subarray(a, b + 1) : buf;
  }

  // The parts of the TIFF header we need: where each 512 x 512 block is.
  async function openTiff(url, signal) {
    const head = await getRange(url, 0, 65535, signal);
    const dv = new DataView(head.buffer, head.byteOffset, head.byteLength);
    if (dv.getUint16(0) !== 0x4949 || dv.getUint16(2, true) !== 42) throw new Error("unexpected height file format");
    const ifd = dv.getUint32(4, true), tags = {};
    for (let i = 0, n = dv.getUint16(ifd, true); i < n; i++) {
      const e = ifd + 2 + 12 * i;
      tags[dv.getUint16(e, true)] = { type: dv.getUint16(e + 2, true), count: dv.getUint32(e + 4, true), at: e + 8 };
    }
    const one = (t, d) => !tags[t] ? d : tags[t].type === 3 ? dv.getUint16(tags[t].at, true) : dv.getUint32(tags[t].at, true);
    const compression = one(259, 1);
    if (one(256) !== TILE_PX || one(322) !== BLOCK || one(323) !== BLOCK || one(258) !== 8 ||
        one(277, 1) !== 1 || one(317, 1) !== 1 || ![1, 8].includes(compression)) {
      throw new Error("unexpected height file layout");
    }
    async function list(t) {
      const { type, count, at } = tags[t], size = type === 3 ? 2 : 4, off = dv.getUint32(at, true);
      let src = dv, base = 0;
      if (off + count * size > head.length) {
        const more = await getRange(url, off, off + count * size - 1, signal);
        src = new DataView(more.buffer, more.byteOffset, more.byteLength);
        base = off;
      }
      const out = new Array(count);
      for (let i = 0; i < count; i++) out[i] = size === 2 ? src.getUint16(off - base + 2 * i, true) : src.getUint32(off - base + 4 * i, true);
      return out;
    }
    return { url, compression, offsets: await list(324), counts: await list(325), across: TILE_PX / BLOCK };
  }

  async function readBlock(t, bx, by, signal) {
    const i = by * t.across + bx, a = t.offsets[i], n = t.counts[i];
    if (!n) return new Uint8Array(BLOCK * BLOCK);           // empty block: GDAL reads it as 0
    const raw = await getRange(t.url, a, a + n - 1, signal);
    if (t.compression === 1) return raw;
    const out = new Response(new Blob([raw]).stream().pipeThrough(new DecompressionStream("deflate")));
    return new Uint8Array(await out.arrayBuffer());
  }

  // ------------------------------------------------------------ counting
  function addStrip(hist, blocks, bx0, c0, c1, ra, rb, k, x0m, y0m, res, b, cells) {
    const step = (b.e - b.w) / cells;
    const ci = new Int32Array(c1 - c0);
    for (let c = c0; c < c1; c++) {
      const v = Math.floor((((x0m + (c + 0.5) * res) / R) * DEG - b.w) / step);
      ci[c - c0] = v >= 0 && v < cells ? v : -1;
    }
    for (let r = ra; r < rb; r++) {
      const lat = (2 * Math.atan(Math.exp((y0m - (r + 0.5) * res) / R)) - Math.PI / 2) * DEG;
      const ri = Math.floor((b.n - lat) / step);
      if (ri < 0 || ri >= cells) continue;
      const lr = r - k * BLOCK;
      for (let c = c0; c < c1; c++) {
        const cc = ci[c - c0];
        if (cc < 0) continue;
        const bi = Math.floor(c / BLOCK) - bx0;
        const v = blocks[bi][lr * BLOCK + (c % BLOCK)];
        hist[(ri * cells + cc) * BINS + (v === NODATA ? BINS - 1 : Math.min(v, BINS - 2))]++;
      }
    }
  }

  // Smallest value whose cumulative share reaches q.
  function pct(h, from, to, q) {
    let total = 0;
    for (let i = from; i < to; i++) total += h[i];
    let c = 0;
    for (let i = from; i < to; i++) { c += h[i]; if (c >= q * total) return i - from; }
    return to - from - 1;
  }

  function stats(hist, cells) {
    const cover = [], tree = [], top = [];
    for (let k = 0; k < cells * cells; k++) {
      const o = k * BINS;
      let seen = 0, n = 0, t = 0;
      for (let i = 0; i < BINS; i++) {
        seen += hist[o + i];
        if (i < BINS - 1) n += hist[o + i];
        if (i > TREE_M && i < BINS - 1) t += hist[o + i];
      }
      if (!seen || n < 0.5 * seen) { cover.push(null); tree.push(null); top.push(null); continue; }
      cover.push(Math.floor(100 * t / n + 0.5));
      tree.push(t ? pct(hist, o + TREE_M + 1, o + BINS - 1, 0.5) + TREE_M + 1 : 0);
      top.push(pct(hist, o, o + BINS - 1, 0.95));
    }
    return { n: cells, cover, tree_m: tree, top_m: top };
  }

  // ------------------------------------------------------------ saved progress
  const DB = "riziki-height", STORE = "progress";
  function db() {
    return new Promise((resolve, reject) => {
      const r = indexedDB.open(DB, 1);
      r.onupgradeneeded = () => r.result.createObjectStore(STORE);
      r.onsuccess = () => resolve(r.result);
      r.onerror = () => reject(r.error);
    });
  }
  async function idb(mode, fn) {
    const d = await db();
    return new Promise((resolve, reject) => {
      const tx = d.transaction(STORE, mode), req = fn(tx.objectStore(STORE));
      tx.oncomplete = () => { d.close(); resolve(req && req.result); };
      tx.onerror = () => { d.close(); reject(tx.error); };
    });
  }
  const loadSaved = (frame) => idb("readonly", (s) => s.get(frame)).catch(() => null);
  const save = (frame, v) => idb("readwrite", (s) => s.put(v, frame)).catch(() => null);
  const clear = (frame) => idb("readwrite", (s) => s.delete(frame)).catch(() => null);

  // ------------------------------------------------------------ the run
  /**
   * frame, bounds {s, w, n, e}, cells, tileURL(quadkey) -> URL,
   * onProgress(done, total), signal (abort = pause).
   * Resolves to the per-cell grid; rejects with AbortError on pause.
   */
  async function run({ frame, bounds, cells = 100, tileURL, onProgress, signal }) {
    const todo = units(bounds);
    const saved = await loadSaved(frame);
    const ok = saved && saved.cells === cells && saved.total === todo.length;
    const hist = ok ? saved.hist : new Uint32Array(cells * cells * BINS);
    const done = new Set(ok ? saved.done : []);
    onProgress?.(done.size, todo.length);
    const files = {};
    for (const [x, y, k] of todo) {
      const key = `${x}/${y}/${k}`;
      if (done.has(key)) continue;
      signal?.throwIfAborted?.();
      const q = quadkey(x, y);
      files[q] = files[q] || await openTiff(tileURL(q), signal);
      const [x0m, y0m, res] = tileOrigin(x, y);
      const [c0, c1, r0, r1] = pixelWindow(bounds, x0m, y0m, res);
      const ra = Math.max(r0, k * BLOCK), rb = Math.min(r1, (k + 1) * BLOCK);
      const bx0 = Math.floor(c0 / BLOCK), bx1 = Math.floor((c1 - 1) / BLOCK);
      const blocks = new Array(bx1 - bx0 + 1);
      for (let i = 0; i < blocks.length; i += PARALLEL) {
        const part = [];
        for (let j = i; j < Math.min(i + PARALLEL, blocks.length); j++) part.push(readBlock(files[q], bx0 + j, k, signal).then((v) => (blocks[j] = v)));
        await Promise.all(part);
      }
      addStrip(hist, blocks, bx0, c0, c1, ra, rb, k, x0m, y0m, res, bounds, cells);
      done.add(key);
      await save(frame, { cells, total: todo.length, done: [...done], hist });
      onProgress?.(done.size, todo.length);
    }
    const grid = stats(hist, cells);
    await clear(frame);
    return grid;
  }

  // Photo dates whose outline overlaps the frame (box test), from the tiles' metadata.
  async function imageDates(bounds, metaURL, signal) {
    const dates = {};
    for (const [x, y] of tilesFor(bounds)) {
      const r = await fetch(metaURL(quadkey(x, y)), { signal, credentials: "same-origin" });
      if (!r.ok) continue;
      for (const f of (await r.json()).features || []) {
        let w = 180, e = -180, s = 90, n = -90;
        JSON.stringify(f.geometry.coordinates).replace(/-?\d+\.?\d*,-?\d+\.?\d*/g, (p) => {
          const [lon, lat] = p.split(",").map(Number);
          w = Math.min(w, lon); e = Math.max(e, lon); s = Math.min(s, lat); n = Math.max(n, lat);
          return p;
        });
        if (w < bounds.e && e > bounds.w && s < bounds.n && n > bounds.s) dates[f.properties.acq_date] = null;
      }
    }
    return dates;
  }

  const hasSaved = async (frame) => { const s = await loadSaved(frame); return s ? { done: s.done.length, total: s.total } : null; };

  window.RizikiHeight = { run, imageDates, hasSaved, clear, units, stats, quadkey, TREE_M };
})();
