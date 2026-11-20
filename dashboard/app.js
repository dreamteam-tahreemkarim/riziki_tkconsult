/*
 * Riziki Prosopis map: the dashboard.
 *
 * Plain JavaScript, no build step. Sections, top to bottom:
 *    1. helpers       small utilities
 *    2. frames        the fixed 0.1 degree grid (same formula as the Python side)
 *    3. saved state   what survives a reload: localStorage + the URL
 *    4. network       fetch with timeout and retry; online / offline
 *    5. data          index.json and scan files, with copies kept on the phone
 *    6. status        what to tell the client, and how fresh the map is
 *    7. map           Leaflet, frame squares, density and change overlays
 *    8. panel         the text side: status, numbers, buttons
 *    9. scans         request -> outbox -> send -> check -> done
 *   10. tree height   estimate on the cloud server or on this device (height.js)
 *   11. extras        GPX download, my location, demo mode
 *   12. start
 *
 * WHY IT COPES WITH BAD INTERNET
 *   - sw.js keeps a copy of the page, so it opens with no connection at all.
 *   - The chosen area, season and comparison live in the URL and on the
 *     phone, so a reload puts you back exactly where you were.
 *   - A scan request is saved BEFORE it is sent. If the connection drops it
 *     waits, retries by itself, and keeps checking for the result after a
 *     reload or the next day.
 *   - Areas you have opened stay on the phone and still open offline.
 *   - A scan is ~12 KB. Frame squares are computed, never downloaded.
 *   - A tree-height estimate is saved too. On the cloud server it keeps going
 *     whatever the phone does; on this device it pauses when the connection
 *     drops and carries on from the last finished strip.
 */
(function () {
  "use strict";

  const C = window.RIZIKI_CONFIG;
  const VIEW = !!C.VIEW_ONLY;                 // stored maps only: no scan or height buttons
  const DEMO = !C.API_URL && !VIEW;
  const KEY = "riziki.";
  // A locked copy with no key on this device: drop any maps saved here earlier.
  if (C.LOCKED && window.RizikiLock && !RizikiLock.hasKey()) RizikiLock.wipe();

  // ---------------------------------------------------------------- 1. helpers
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const fmt = (n) => (n == null || isNaN(n) ? "–" : Math.round(n).toLocaleString("en"));
  const day = (iso) => !iso ? "–" : new Date(iso.length === 10 ? iso + "T00:00:00Z" : iso)
    .toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric", timeZone: "UTC" });
  const when = (iso) => new Date(iso).toLocaleString("en-GB",
    { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
  const monthName = (ym) => new Date(ym + "-01T00:00:00Z")
    .toLocaleDateString("en-GB", { month: "short", year: "numeric", timeZone: "UTC" });
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const uuid = () => (window.crypto && crypto.randomUUID ? crypto.randomUUID()
    : Date.now().toString(36) + Math.random().toString(36).slice(2));

  function toast(msg, ms = 3500) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (t.hidden = true), ms);
  }

  // ----------------------------------------------------------------- 2. frames
  // A frame is named by its south-west corner in hundredths of a degree:
  // N0040_E03600 covers 0.40-0.50 N, 36.00-36.10 E.
  const PER = Math.round(1 / C.FRAME_DEG);          // frames per degree
  const r9 = (x) => Math.round(x * 1e9) / 1e9;      // 0.3 * 10 is 2.9999..., so round first
  const pad = (n, w) => String(n).padStart(w, "0");

  function frameId(lat, lon) {
    const s = Math.floor(r9(lat * PER)) / PER;
    const w = Math.floor(r9(lon * PER)) / PER;
    return (s >= 0 ? "N" : "S") + pad(Math.abs(Math.round(s * 100)), 4) + "_" +
           (w >= 0 ? "E" : "W") + pad(Math.abs(Math.round(w * 100)), 5);
  }

  function frameBounds(id) {
    const m = /^([NS])(\d{4})_([EW])(\d{5})$/.exec(id || "");
    if (!m) return null;
    const s = (m[1] === "N" ? 1 : -1) * Number(m[2]) / 100;
    const w = (m[3] === "E" ? 1 : -1) * Number(m[4]) / 100;
    return { s, w, n: r9(s + C.FRAME_DEG), e: r9(w + C.FRAME_DEG) };
  }

  function frameName(id) {
    const b = frameBounds(id);
    const lat = (b.s + b.n) / 2, lon = (b.w + b.e) / 2;
    return `${Math.abs(lat).toFixed(2)}° ${lat >= 0 ? "N" : "S"}, ` +
           `${Math.abs(lon).toFixed(2)}° ${lon >= 0 ? "E" : "W"}`;
  }

  function inRegion(id) {
    const b = frameBounds(id), R = C.REGION, e = 1e-9;
    return !!b && b.s >= R.south - e && b.n <= R.north + e && b.w >= R.west - e && b.e <= R.east + e;
  }

  const box = (b) => [[b.s, b.w], [b.n, b.e]];

  // ------------------------------------------------------------ 3. saved state
  const store = {
    get(k, fallback) {
      try { const v = localStorage.getItem(KEY + k); return v ? JSON.parse(v) : fallback; }
      catch (e) { return fallback; }
    },
    set(k, v) {
      try { localStorage.setItem(KEY + k, JSON.stringify(v)); }
      catch (e) { /* storage full or blocked: carry on without saving */ }
    },
    del(k) { try { localStorage.removeItem(KEY + k); } catch (e) { /* ignore */ } },
    keys(prefix) {
      const out = [];
      try {
        for (let i = 0; i < localStorage.length; i++) {
          const k = localStorage.key(i);
          if (k && k.startsWith(KEY + prefix)) out.push(k.slice(KEY.length));
        }
      } catch (e) { /* ignore */ }
      return out;
    },
  };

  const state = {
    index: store.get("index", null),   // last good index.json
    sel: null,                         // { frame, season, compare }
    scan: null,                        // scan shown on the map
    compare: null,                     // older or newer scan to compare with
    change: null,                      // computed difference between the two
    loading: false,
    outbox: VIEW ? [] : store.get("outbox", []),   // scan requests, sent or waiting
    recent: store.get("recent", []),   // areas opened, newest first
    collapsed: false,
    signedOut: false,
    height: null,                      // tree-height estimate for the area shown
    hjob: VIEW ? null : store.get("hjob", null),   // the height estimate running, if any
    hmode: store.get("hmode", "cloud"),
  };

  // The URL holds the selection so a reload, or a link sent to a colleague,
  // opens the same thing:  #N0040_E03600/2026/2016  (frame / season / compare)
  function readHash() {
    const p = decodeURIComponent(location.hash.replace(/^#/, "")).split("/");
    if (!frameBounds(p[0])) return null;
    return { frame: p[0], season: Number(p[1]) || null, compare: Number(p[2]) || null };
  }

  function saveSelection() {
    const s = state.sel;
    const hash = s ? `#${s.frame}/${s.season}${s.compare ? "/" + s.compare : ""}` : "";
    try { history.replaceState(null, "", location.pathname + location.search + hash); } catch (e) { /* sandboxed page */ }
    store.set("sel", s);
  }

  // ----------------------------------------------------------------- 4. network
  class SignedOut extends Error {}

  function setNet(mode) {
    const el = $("net");
    el.textContent = { online: "Online", weak: "Weak connection", offline: "Offline" }[mode];
    el.classList.toggle("off", mode !== "online");
  }

  // GET or POST JSON. Times out, retries (1 s, then 3 s), and tells a dropped
  // connection apart from an expired sign-in. Returns null for "not found".
  async function fetchJSON(url, { timeout = 12000, tries = 3, method = "GET", body } = {}) {
    let lastError;
    for (let i = 0; i < tries; i++) {
      const ctl = new AbortController();
      const timer = setTimeout(() => ctl.abort(), timeout);
      try {
        const r = await fetch(url, {
          method, body, signal: ctl.signal, cache: "no-store",
          redirect: "manual", credentials: "same-origin",
          headers: body ? { "Content-Type": "application/json" } : undefined,
        });
        clearTimeout(timer);
        // A login service answers an expired session with a redirect or 401/403.
        if (r.type === "opaqueredirect" || r.status === 401 || r.status === 403) throw new SignedOut();
        if (r.status === 404) { setNet("online"); return null; }
        if (!r.ok) throw new Error("HTTP " + r.status);
        // sw.js marks answers it gave from the phone's saved copy.
        setNet(r.headers.get("X-Riziki-Saved") ? (navigator.onLine ? "weak" : "offline") : "online");
        const text = await r.text();
        let data;
        try { data = text ? JSON.parse(text) : {}; }
        catch (e) { throw new SignedOut(); }   // a web page instead of data: usually a login page
        return data?.riziki_locked ? await RizikiLock.open(data) : data;   // an encrypted data file
      } catch (e) {
        clearTimeout(timer);
        if (e instanceof SignedOut) { onSignedOut(); throw e; }
        lastError = e;
        if (i < tries - 1) await sleep(1000 * 3 ** i);
      }
    }
    setNet(navigator.onLine ? "weak" : "offline");
    throw lastError;
  }

  function onSignedOut() {
    if (state.signedOut) return;
    state.signedOut = true;
    setBanner();
  }

  // -------------------------------------------------------------------- 5. data
  async function loadIndex() {
    try {
      const idx = await fetchJSON(C.DATA_URL + "index.json", { tries: 2 });
      if (idx) { state.index = idx; store.set("index", idx); }
    } catch (e) { /* offline: keep the saved copy */ }
    if (DEMO) mergeDemoScans();
    drawFrames();
    if (state.sel && !state.scan) loadSelection();
    else render();
  }

  const scanKey = (f, s) => `scan:${f}:${s}`;

  // One scan. Tries the network; falls back to the copy saved on the phone.
  // fresh = true when checking for a result we're waiting on.
  async function loadScan(frame, season, { fresh = false } = {}) {
    if (DEMO) {
      const d = store.get(`demo:${frame}:${season}`, null);
      if (d) return d;
    }
    const listed = state.index?.frames?.[frame]?.scans?.includes(season);
    const saved = store.get(scanKey(frame, season), null);
    if (!listed && !fresh) return saved;
    try {
      const sc = await fetchJSON(`${C.DATA_URL}scans/${frame}/${season}.json?t=${Date.now()}`,
                                 { tries: fresh ? 1 : 3 });
      if (sc) keep(sc);
      return sc || saved;
    } catch (e) {
      if (saved) return saved;
      throw e;
    }
  }

  // Keep the last 12 scans opened on the phone, for offline use.
  function keep(sc) {
    store.set(scanKey(sc.frame, sc.season), sc);
    state.recent = [{ frame: sc.frame, season: sc.season, status: sc.status },
      ...state.recent.filter((r) => !(r.frame === sc.frame && r.season === sc.season))];
    for (const old of state.recent.slice(12)) store.del(scanKey(old.frame, old.season));
    state.recent = state.recent.slice(0, 12);
    store.set("recent", state.recent);
  }

  function addToIndex(sc) {
    const idx = state.index || (state.index = { frames: {} });
    idx.frames = idx.frames || {};
    const fr = idx.frames[sc.frame] || (idx.frames[sc.frame] = { scans: [] });
    if (!fr.scans.includes(sc.season)) fr.scans.push(sc.season);
    fr.scans.sort((a, b) => b - a);
    if (sc.season === fr.scans[0]) {
      fr.status = sc.status;
      fr.images_to = sc.images?.dry?.[1];
      fr.newest_image = fr.newest_image && fr.newest_image > fr.images_to ? fr.newest_image : fr.images_to;
    }
  }

  // ------------------------------------------------------------------ 6. status
  const STATUS = {
    plan:   { label: "Plan crews",       say: "Good enough to plan a full team. Take the plot list." },
    scout:  { label: "Scout first",      say: "Send 2–3 people to check the points before sending a team." },
    cannot: { label: "Can't judge here", say: "Don't send anyone based on this map." },
    none:   { label: "Not scanned",      say: VIEW ? "There's no map of this area yet." : "Scan this area to see where Prosopis is." },
  };

  // The status the scanner gave, made stricter if the map has gone stale.
  function statusOf(sc) {
    if (!sc) return { code: "none", reasons: [] };
    let code = STATUS[sc.status] ? sc.status : "cannot";
    const reasons = [...(sc.status_reasons || [])];
    const latest = state.index?.latest_season;
    if (latest && sc.season < latest && code === "plan") {
      code = "scout";
      reasons.unshift(`This map is from ${sc.season}. Scan the ${latest} season before sending a team.`);
    }
    if (sc.refresh_failed) reasons.unshift(`The last re-scan failed (${sc.refresh_failed.message}) Showing the previous scan.`);
    if (sc.demo) reasons.unshift("Demo data, not a real scan.");
    return { code, reasons };
  }

  function freshness(frame, sc) {
    const idx = state.index || {};
    const fr = idx.frames?.[frame];
    const checked = idx.checked ? ` Checked ${when(idx.checked)}.` : "";
    if (!sc) {
      if (VIEW) return "";
      return "Not scanned for this season yet." +
        (idx.latest_season ? ` Newest season available: ${idx.latest_season} (Jan–Aug).` : "");
    }
    if (sc.failed) return "";
    const to = sc.images?.to || sc.images?.wet?.[1] || sc.images?.dry?.[1];
    const said = `Map from the ${sc.season} season (dry Jan–Mar + wet Apr–Aug), images up to ${day(to)}.`;
    if (idx.latest_season && sc.season < idx.latest_season) {
      return said + ` A newer season (${idx.latest_season}) is available.`;
    }
    if (fr?.newest_image && to && fr.newest_image > to) {
      return said + ` Newer images were published (${day(fr.newest_image)}).` +
        (VIEW ? " A new scan would include them." : " Scan again for the latest.") + checked;
    }
    return said + " No newer images have been published." + checked +
      (idx.next_update ? ` Next update possible from ${monthName(idx.next_update)}.` : "");
  }

  // --------------------------------------------------------------------- 7. map
  // Colours for the map layers, as [r, g, b, alpha 0-255].
  const DENSITY = { "1": [216, 207, 138, 175], "2": [168, 147, 0, 205], "3": [72, 69, 0, 230], "9": [140, 141, 137, 150] };
  const CHANGE = { c: [38, 66, 39, 230], n: [180, 69, 31, 230], s: [72, 69, 0, 110], u: [140, 141, 137, 140] };
  const STATUS_COLOUR = { plan: "#264227", scout: "#a89300", cannot: "#b4451f" };
  const css = (rgba) => `rgba(${rgba[0]},${rgba[1]},${rgba[2]},${(rgba[3] / 255).toFixed(2)})`;

  let map, frameLayer, overviewLayer, plotLayer, gridImage, outline, meDot, hintEl;

  function initMap() {
    map = L.map("map", { preferCanvas: true, minZoom: 5, maxZoom: C.BASEMAP.maxZoom });
    L.tileLayer(C.BASEMAP.url, {
      attribution: C.BASEMAP.attribution, maxZoom: C.BASEMAP.maxZoom, crossOrigin: "anonymous",
      ...(C.BASEMAP.maxNativeZoom ? { maxNativeZoom: C.BASEMAP.maxNativeZoom } : {}),
    }).addTo(map);
    // The density picture sits under the frame lines.
    map.createPane("grid").style.zIndex = 350;

    const R = C.REGION;
    map.setMaxBounds([[R.south - 4, R.west - 4], [R.north + 4, R.east + 4]]);
    frameLayer = L.layerGroup().addTo(map);
    overviewLayer = L.layerGroup().addTo(map);
    plotLayer = L.layerGroup().addTo(map);

    const Locate = L.Control.extend({
      options: { position: "topleft" },
      onAdd() {
        const b = L.DomUtil.create("button", "map-btn leaflet-bar");
        b.type = "button";
        b.textContent = "My location";
        L.DomEvent.disableClickPropagation(b);
        b.addEventListener("click", locate);
        return b;
      },
    });
    map.addControl(new Locate());
    hintEl = L.DomUtil.create("div", "hint", map.getContainer());

    const v = store.get("view", null);
    if (v) map.setView([v.lat, v.lon], v.z);
    else map.fitBounds([[R.south, R.west], [R.north, R.east]]);

    map.on("moveend", () => {
      const c = map.getCenter();
      store.set("view", { lat: c.lat, lon: c.lng, z: map.getZoom() });
      drawFrames();
    });
    map.on("click", onMapClick);
  }

  function hint(text) {
    hintEl.textContent = text;
    hintEl.hidden = !text;
  }

  // Zoomed in: draw every frame square in view (scanned ones tinted).
  // Zoomed out: a dot for each scanned frame.
  function drawFrames() {
    if (!map) return;
    frameLayer.clearLayers();
    overviewLayer.clearLayers();
    const frames = state.index?.frames || {};
    if (map.getZoom() >= C.MIN_FRAME_ZOOM) {
      const b = map.getBounds(), R = C.REGION;
      const s0 = Math.max(b.getSouth(), R.south), n0 = Math.min(b.getNorth(), R.north);
      const w0 = Math.max(b.getWest(), R.west), e0 = Math.min(b.getEast(), R.east);
      let count = 0;
      for (let lat = Math.floor(r9(s0 * PER)) / PER; lat < n0 && count < 800; lat = r9(lat + C.FRAME_DEG)) {
        for (let lon = Math.floor(r9(w0 * PER)) / PER; lon < e0 && count < 800; lon = r9(lon + C.FRAME_DEG)) {
          const id = frameId(lat + C.FRAME_DEG / 2, lon + C.FRAME_DEG / 2);
          const f = frames[id];
          L.rectangle(box(frameBounds(id)), {
            interactive: false, weight: 0.8, color: "#484500", opacity: 0.45,
            fill: !!f, fillColor: f ? STATUS_COLOUR[f.status] || "#8c8d89" : undefined, fillOpacity: 0.16,
          }).addTo(frameLayer);
          count++;
        }
      }
      hint(state.sel ? "" : "Tap a square to choose it");
    } else {
      for (const [id, f] of Object.entries(frames)) {
        const fb = frameBounds(id);
        if (!fb) continue;
        L.circleMarker([(fb.s + fb.n) / 2, (fb.w + fb.e) / 2], {
          radius: 7, weight: 2, color: "#fafbf1", fillColor: STATUS_COLOUR[f.status] || "#8c8d89", fillOpacity: 1,
          bubblingMouseEvents: false,
        }).on("click", () => selectFrame(id)).addTo(overviewLayer);
      }
      hint(state.sel ? "" : "Zoom in to choose an area");
    }
  }

  function onMapClick(e) {
    const id = frameId(e.latlng.lat, e.latlng.lng);
    if (!inRegion(id)) return toast("That's outside the area this map covers.");
    if (map.getZoom() < C.MIN_FRAME_ZOOM) {
      map.setView(e.latlng, C.MIN_FRAME_ZOOM + 1);
      return;
    }
    selectFrame(id);
  }

  // An n x n grid of one-character codes, painted as a tiny picture.
  function gridPicture(codes, n, colours) {
    const cv = document.createElement("canvas");
    cv.width = cv.height = n;
    const ctx = cv.getContext("2d");
    const img = ctx.createImageData(n, n);
    for (let k = 0; k < n * n; k++) {
      const c = colours[codes[k]];
      if (c) img.data.set(c, k * 4);
    }
    ctx.putImageData(img, 0, 0);
    return cv.toDataURL();
  }

  // Cell by cell, older scan vs newer scan of the same frame.
  //   c cleared (was Prosopis, now not)   n new spread   s still Prosopis   u can't compare
  function compareScans(a, b) {
    const [older, newer] = a.season <= b.season ? [a, b] : [b, a];
    const A = older.grid.codes, B = newer.grid.codes;
    if (A.length !== B.length) return null;
    const isP = (ch) => ch === "1" || ch === "2" || ch === "3";
    const tally = { c: 0, n: 0, s: 0, u: 0 };
    let codes = "";
    for (let i = 0; i < A.length; i++) {
      const x = A[i], y = B[i];
      let k = "-";
      if (x === "9" || y === "9") k = "u";
      else if (isP(x) && !isP(y)) k = "c";
      else if (!isP(x) && isP(y)) k = "n";
      else if (isP(x)) k = "s";
      if (k !== "-") tally[k]++;
      codes += k;
    }
    const ha = newer.cell_ha || older.cell_ha || 0;
    return {
      codes, older: older.season, newer: newer.season,
      ha: { cleared: tally.c * ha, spread: tally.n * ha, still: tally.s * ha, unknown: tally.u * ha },
    };
  }

  function drawSelection() {
    if (gridImage) map.removeLayer(gridImage);
    if (outline) map.removeLayer(outline);
    gridImage = outline = null;
    plotLayer.clearLayers();
    state.change = null;
    const s = state.sel;
    if (!s) return;
    const b = box(frameBounds(s.frame));
    const sc = state.scan;
    if (sc?.grid?.codes) {
      if (state.compare?.grid) state.change = compareScans(state.compare, sc);
      const pic = state.change
        ? gridPicture(state.change.codes, sc.grid.n, CHANGE)
        : gridPicture(sc.grid.codes, sc.grid.n, DENSITY);
      gridImage = L.imageOverlay(pic, b, { pane: "grid", className: "pixelated", interactive: false }).addTo(map);
      if (!state.change) {
        for (const p of sc.plots || []) {
          L.circleMarker([p.lat, p.lon], {
            radius: 6, weight: 2, color: "#fafbf1",
            fillColor: p.kind === "check" ? "#a89300" : "#264227", fillOpacity: 1,
          }).bindTooltip(`${esc(p.id)} · ${esc(p.class)}`).addTo(plotLayer);
        }
      }
    }
    outline = L.rectangle(b, { interactive: false, color: "#264227", weight: 3, fill: false }).addTo(map);
  }

  // ------------------------------------------------------------------- 8. panel
  function render() {
    $("panel").classList.toggle("collapsed", state.collapsed);
    $("panel-body").innerHTML = state.sel ? areaView(state.sel) : homeView();
  }

  function homeView() {
    const recent = state.recent.slice(0, 6);
    let h = `<p class="eyebrow">Prosopis map</p><h1>Where is the Prosopis?</h1><div class="more">
      <p>Zoom in until squares appear, then tap one. Each square is about 11 × 11 km.</p>
      <button class="btn" data-act="locate">Use my location</button>`;
    if (state.outbox.length) {
      h += `<h2>Scans in progress</h2><ul class="recent">${state.outbox.map((j) =>
        `<li><button data-act="open" data-f="${esc(j.frame)}" data-s="${j.season}">
          <span>${esc(frameName(j.frame))} · ${j.season}</span><span>${j.sent ? "scanning" : "waiting to send"}</span></button></li>`).join("")}</ul>`;
    }
    const hj = state.hjob;
    if (hj) {
      h += `<h2>Tree height in progress</h2><ul class="recent"><li><button data-act="open" data-f="${esc(hj.frame)}">
        <span>${esc(frameName(hj.frame))}</span><span>${hj.phase === "paused" ? "paused" : `${heightPct(hj)}%`}</span></button></li></ul>`;
    }
    if (recent.length) {
      h += `<h2>Opened recently · work offline</h2><ul class="recent">${recent.map((r) =>
        `<li><button data-act="open" data-f="${esc(r.frame)}" data-s="${r.season}">
          <span>${esc(frameName(r.frame))} · ${r.season}</span><span>${esc(STATUS[r.status]?.label || "")}</span></button></li>`).join("")}</ul>`;
    }
    return h + `</div>`;
  }

  function areaView(s) {
    const sc = state.scan;
    const st = statusOf(sc);
    const job = findJob(s.frame, s.season);
    const scanned = state.index?.frames?.[s.frame]?.scans || [];

    let h = `<p class="eyebrow">Area ${esc(s.frame)}</p><h1>${esc(frameName(s.frame))}</h1>`;
    if (state.loading && !sc) h += `<p class="small"><span class="spin"></span>Loading…</p>`;
    h += `<div class="status status-${st.code}">
      <strong>${STATUS[st.code].label}${sc?.demo ? '<span class="demo-tag">DEMO</span>' : ""}</strong>
      <span>${STATUS[st.code].say}</span>
      ${st.reasons.length ? `<ul>${st.reasons.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
    </div><div class="more">`;

    if (job) h += pendingBox(job);
    const fresh = freshness(s.frame, sc);
    if (fresh) h += `<p class="small">${esc(fresh)}</p>`;
    if (sc?.grid) h += state.change ? changeFigures(state.change) : densityFigures(sc);

    if (!VIEW || scanned.length) {
      h += `<label for="season">Season</label>
        <select id="season" data-act="season">${seasonOptions(s.season, scanned)}</select>`;
    }
    if (!VIEW) h += `<button class="btn" data-act="scan" ${job ? "disabled" : ""}>${sc ? "Scan again" : "Scan this season"}</button>`;

    const others = scanned.filter((y) => y !== s.season);
    if (sc && others.length) {
      h += `<label for="compare">Compare with</label><select id="compare" data-act="compare">
        <option value="">No comparison</option>
        ${others.map((y) => `<option value="${y}" ${y === s.compare ? "selected" : ""}>${y} season</option>`).join("")}
      </select>`;
    }
    if (sc?.plots?.length && !state.change) {
      const what = sc.plots[0].kind === "check" ? "check points" : "crew plots";
      h += `<button class="btn ghost" data-act="gpx">Download ${what} for GPS (${sc.plots.length})</button>`;
    }
    h += heightBlock(s);
    if (sc) h += scanDetails(sc);
    return h + `<button class="link" data-act="close">← Choose another area</button></div>`;
  }

  function densityFigures(sc) {
    const a = sc.area_ha || {}, d = sc.density_ha || {};
    const codes = sc.grid?.codes || "";
    const unsure = codes ? (codes.split("9").length - 1) / codes.length : 0;
    return `<dl class="figures">
      <dt class="big">Prosopis</dt><dd class="big">${a.low != null ? `${fmt(a.low)}–${fmt(a.high)}` : fmt(a.prosopis)} ha</dd>
      <dt class="sub">Ground classed as</dt><dd></dd>
      <dt><span class="sw" style="background:${css(DENSITY[3])}"></span>Thick</dt><dd>${fmt(d.thick)} ha</dd>
      <dt><span class="sw" style="background:${css(DENSITY[2])}"></span>Medium</dt><dd>${fmt(d.medium)} ha</dd>
      <dt><span class="sw" style="background:${css(DENSITY[1])}"></span>Thin</dt><dd>${fmt(d.thin)} ha</dd>
      <dt><span class="sw" style="background:${css(DENSITY[9])}"></span>Can't judge</dt><dd>${Math.round(unsure * 100)}% of area</dd>
    </dl>${a.low != null && !sc.demo ? `<p class="small">The real area fell in a range like this 8 times in 10 when tested in Baringo.</p>` : ""}`;
  }

  function changeFigures(ch) {
    return `<p class="small">Change from the ${ch.older} season to the ${ch.newer} season, cell by cell.</p>
    <dl class="figures">
      <dt><span class="sw" style="background:${css(CHANGE.c)}"></span>Cleared</dt><dd>${fmt(ch.ha.cleared)} ha</dd>
      <dt><span class="sw" style="background:${css(CHANGE.n)}"></span>New spread</dt><dd>${fmt(ch.ha.spread)} ha</dd>
      <dt><span class="sw" style="background:${css(CHANGE.s)}"></span>Still Prosopis</dt><dd>${fmt(ch.ha.still)} ha</dd>
      <dt><span class="sw" style="background:${css(CHANGE.u)}"></span>Can't compare</dt><dd>${fmt(ch.ha.unknown)} ha</dd>
    </dl>`;
  }

  function seasonOptions(selected, scanned) {
    const top = state.index?.latest_season || new Date().getFullYear();
    let o = "";
    for (let y = top; y >= C.OLDEST_SEASON; y--) {
      if (VIEW && !scanned.includes(y)) continue;   // only the seasons there is a map for
      o +=`<option value="${y}" ${y === selected ? "selected" : ""}>${y} season${scanned.includes(y) && !VIEW ? " · scanned" : ""}</option>`;
    }
    return o;
  }

  function scanDetails(sc) {
    const im = sc.images || {};
    return `<details><summary>Scan details</summary><dl>
      <dt>Images</dt><dd>${esc(im.sensor)}, ${esc(im.n_scenes ?? "?")} scenes</dd>
      <dt>Dry season</dt><dd>${day(im.dry?.[0])} – ${day(im.dry?.[1])}</dd>
      <dt>Wet season</dt><dd>${day(im.wet?.[0])} – ${day(im.wet?.[1])}</dd>
      <dt>Model</dt><dd>${esc(sc.model?.id)}</dd>
      <dt>Trained on</dt><dd>${esc(sc.model?.trained_on)}</dd>
      <dt>Familiar land</dt><dd>${Math.round((sc.applicability ?? 0) * 100)}% of the area looks like land the model learned from</dd>
      ${sc.model?.spatial_cv_accuracy ? `<dt>Tested accuracy</dt><dd>${Math.round(sc.model.spatial_cv_accuracy * 100)}% of held-out Baringo pixels right</dd>` : ""}
      <dt>Made</dt><dd>${sc.created ? when(sc.created) : "–"}</dd>
    </dl></details>`;
  }

  function pendingBox(j) {
    const mins = Math.max(0, Math.round((Date.now() - Date.parse(j.at)) / 60000));
    if (!j.sent) {
      return `<div class="pending"><span class="spin"></span><strong>Scan saved, waiting to send.</strong>
        ${navigator.onLine ? esc(j.error || "Sending…") : "You're offline. It sends by itself when the connection is back."}
        <button class="link" data-act="cancel" data-id="${esc(j.id)}">Cancel</button></div>`;
    }
    return `<div class="pending"><span class="spin"></span><strong>Scanning…</strong>
      Asked ${when(j.at)} (${mins} min ago). Usually 5–15 minutes. You can close this page;
      the result will be here when you come back.${mins > 45 ? " This is taking longer than usual." : ""}</div>`;
  }

  function setBanner() {
    const b = $("banner");
    let msg = "";
    if (state.signedOut) msg = `Your sign-in has expired. Anything you asked for is saved. <button data-b="signin">Sign in again</button>`;
    else if (DEMO) msg = "DEMO: scans and tree-height runs you ask for here are simulated. Don't send anyone based on them.";
    b.innerHTML = msg;
    b.hidden = !msg;
    document.body.classList.toggle("has-banner", !!msg);
    if (map) map.invalidateSize();
  }

  // Selecting an area.
  async function selectFrame(id, season, compare) {
    const fr = state.index?.frames?.[id];
    season = season || fr?.scans?.[0] || state.index?.latest_season || new Date().getFullYear();
    if (VIEW && fr?.scans?.length && !fr.scans.includes(season)) season = fr.scans[0];
    state.sel = { frame: id, season, compare: compare || null };
    state.scan = state.compare = state.height = null;
    state.collapsed = false;
    saveSelection();
    drawSelection();
    render();
    drawFrames();
    const b = box(frameBounds(id));
    if (map.getZoom() < C.MIN_FRAME_ZOOM || !map.getBounds().contains(b)) {
      const below = window.innerWidth < 900 ? $("panel").offsetHeight : 0;
      map.fitBounds(b, { paddingTopLeft: [20, 20], paddingBottomRight: [20, below + 20] });
    }
    await loadSelection();
  }

  async function loadSelection() {
    const s = state.sel;
    if (!s) return;
    state.loading = true;
    render();
    let scan = null, compare = null, height = null;
    try {
      scan = await loadScan(s.frame, s.season);
      compare = s.compare && scan ? await loadScan(s.frame, s.compare) : null;
    } catch (e) {
      if (!(e instanceof SignedOut)) toast("Couldn't load this area. Showing what's saved on this phone.");
    }
    height = await loadHeight(s.frame);
    if (state.sel !== s) return;              // they picked something else meanwhile
    state.scan = scan;
    state.compare = compare;
    state.height = height;
    state.loading = false;
    if (scan) keep(scan);
    drawSelection();
    render();
  }

  function closeArea() {
    state.sel = state.scan = state.compare = state.height = null;
    saveSelection();
    drawSelection();
    render();
    drawFrames();
  }

  function wirePanel() {
    const body = $("panel-body");
    body.addEventListener("click", (e) => {
      const el = e.target.closest("[data-act]");
      if (!el || el.tagName === "SELECT") return;
      const act = el.dataset.act;
      if (act === "locate") locate();
      else if (act === "open") selectFrame(el.dataset.f, Number(el.dataset.s) || undefined);
      else if (act === "scan") requestScan(state.sel.frame, state.sel.season);
      else if (act === "gpx") downloadGPX(state.scan);
      else if (act === "close") closeArea();
      else if (act === "cancel") cancelJob(el.dataset.id);
      else if (act === "height") startHeight(state.sel.frame);
      else if (act === "hpause") pauseHeight();
      else if (act === "hresume") resumeHeight();
      else if (act === "hcancel") cancelHeight();
    });
    body.addEventListener("change", (e) => {
      const act = e.target.dataset.act, v = Number(e.target.value) || null;
      if (act === "season") selectFrame(state.sel.frame, v);
      if (act === "hmode") { state.hmode = e.target.value; store.set("hmode", state.hmode); }
      if (act === "compare") {
        state.sel.compare = v;
        saveSelection();
        loadSelection();
      }
    });
    $("sheet").addEventListener("click", () => { state.collapsed = !state.collapsed; render(); });
    $("banner").addEventListener("click", (e) => {
      if (e.target.dataset.b === "signin") location.reload();
    });
  }

  // ------------------------------------------------------------------- 9. scans
  // Every request goes into the outbox first and is saved on the phone, so
  // nothing is lost to a dropped connection or a reload.
  const findJob = (f, s) => state.outbox.find((j) => j.frame === f && j.season === s);
  const saveOutbox = () => store.set("outbox", state.outbox);

  function requestScan(frame, season) {
    if (findJob(frame, season)) return;
    state.outbox.push({ id: uuid(), frame, season, at: new Date().toISOString(), sent: false, tries: 0 });
    saveOutbox();
    render();
    flushOutbox();
  }

  function cancelJob(id) {
    state.outbox = state.outbox.filter((j) => !(j.id === id && !j.sent));
    saveOutbox();
    render();
  }

  let flushing = false;
  async function flushOutbox() {
    if (flushing) return;
    flushing = true;
    try {
      for (const j of state.outbox.filter((x) => !x.sent)) {
        if (!navigator.onLine) break;
        try {
          if (DEMO) await sleep(600);
          else {
            await fetchJSON(C.API_URL.replace(/\/$/, "") + "/scan", {
              method: "POST", tries: 2, timeout: 15000,
              body: JSON.stringify({ frame: j.frame, season: j.season, request_id: j.id }),
            });
          }
          j.sent = true;
          j.sentAt = new Date().toISOString();
          j.error = null;
        } catch (e) {
          j.tries++;
          j.error = e instanceof SignedOut ? "Sign in again to send it." : "Couldn't reach the server yet. Retrying by itself.";
          if (e instanceof SignedOut) break;
        } finally {
          saveOutbox();
          render();
        }
      }
    } finally {
      flushing = false;
    }
    schedulePoll(DEMO ? 4000 : 20000);
  }

  let pollTimer = null;
  function schedulePoll(ms = 20000) {
    clearTimeout(pollTimer);
    if (state.outbox.length) pollTimer = setTimeout(pollOnce, ms);
  }

  // Check whether any scan we're waiting on has arrived.
  async function pollOnce() {
    if (document.hidden) return schedulePoll();
    if (state.outbox.some((j) => !j.sent)) flushOutbox();
    let changed = false;
    for (const j of state.outbox.filter((x) => x.sent)) {
      if (DEMO && Date.now() - Date.parse(j.sentAt) > 8000) {
        store.set(`demo:${j.frame}:${j.season}`, demoScan(j));
      }
      let sc = null;
      try { sc = await loadScan(j.frame, j.season, { fresh: true }); } catch (e) { /* try again later */ }
      // The scanner copies request_id into the result, so this is the answer
      // to THIS request, whatever the phone's clock says.
      const isAnswer = sc && (sc.request_id ? sc.request_id === j.id
        : Date.parse(sc.created) > Date.parse(j.at) - 10 * 60000);
      if (isAnswer) { finishJob(j, sc); changed = true; }
      else if (Date.now() - Date.parse(j.at) > 48 * 3600e3) {
        state.outbox = state.outbox.filter((x) => x !== j);
        toast(`No result for ${frameName(j.frame)} after 2 days. Please ask again.`, 6000);
        changed = true;
      }
    }
    saveOutbox();
    if (changed) render();
    schedulePoll();
  }

  function finishJob(j, sc) {
    state.outbox = state.outbox.filter((x) => x !== j);
    saveOutbox();
    keep(sc);
    addToIndex(sc);
    store.set("index", state.index);
    drawFrames();
    toast(`Scan ready: ${frameName(j.frame)}, ${j.season}`);
    if (state.sel?.frame === j.frame && state.sel?.season === j.season) {
      state.scan = sc;
      drawSelection();
    }
  }

  // ------------------------------------------------------------ 10. tree height
  // "Estimate tree height" reads the free Meta/WRI canopy height map (an AI
  // estimate from 2018-2020 satellite photos) and sums it up per cell.
  //   cloud server  POST /api/height starts .github/workflows/height.yml; the bar
  //                 follows GET /api/progress; the result is data/scans/<f>/height.json
  //   this device   height.js does the same work here, through /api/chm; Pause,
  //                 and a lost connection, stop after the current strip
  const API = (C.API_URL || "").replace(/\/$/, "");
  const HEIGHT_SOURCE = { name: "Meta/WRI Canopy Height Maps v2 (CHMv2)", licence: "CC BY 4.0",
    url: "https://registry.opendata.aws/dataforgood-fb-forestsv2/", paper: "Brandt et al. 2026, arXiv:2603.06382" };
  const HEIGHT_MAE = 3;                         // m, published error against airborne lidar
  const canDevice = () => "DecompressionStream" in window && "indexedDB" in window && !!window.RizikiHeight;
  const saveHjob = () => store.set("hjob", state.hjob);
  let deviceCtl = null, hTimer = null;

  async function loadHeight(frame) {
    const saved = store.get(`height:${frame}`, null);
    if (!state.index?.frames?.[frame]?.height) return saved;
    try {
      const h = await fetchJSON(`${C.DATA_URL}scans/${frame}/height.json`, { tries: 2 });
      if (h) { keepHeight(h); return h; }
    } catch (e) { /* offline: use the saved copy */ }
    return saved;
  }

  // Keep the last 8 height estimates on the phone (~100 KB each).
  function keepHeight(h) {
    store.set(`height:${h.frame}`, h);
    const list = [h.frame, ...store.get("hrecent", []).filter((f) => f !== h.frame)];
    for (const old of list.slice(8)) store.del(`height:${old}`);
    store.set("hrecent", list.slice(0, 8));
  }

  // The numbers shown: over the cells mapped as Prosopis if there are enough, else the whole area.
  function heightSummary(h, sc) {
    const g = h.grid, n = g.n * g.n, codes = sc?.grid?.codes;
    let cells = [];
    if (codes && codes.length === n) for (let i = 0; i < n; i++) if (codes[i] >= "1" && codes[i] <= "3") cells.push(i);
    const inProsopis = cells.length >= 20;
    if (!inProsopis) cells = Array.from({ length: n }, (_, i) => i);
    const ok = cells.filter((i) => g.cover[i] != null);
    const treed = ok.filter((i) => g.cover[i] >= 5).map((i) => g.tree_m[i]).sort((a, b) => a - b);
    const q = (p) => treed.length ? treed[Math.min(treed.length - 1, Math.floor(p * treed.length))] : null;
    return { inProsopis, typical: q(0.5), lo: q(0.25), hi: q(0.75),
             cover: ok.length ? ok.reduce((a, i) => a + g.cover[i], 0) / ok.length : null };
  }

  function photoDates(h) {
    const d = Object.keys(h.image_dates || {}).filter((x) => /^\d{4}-\d{2}/.test(x)).sort();
    if (!d.length) return "2017–2020";
    const a = monthName(d[0].slice(0, 7)), b = monthName(d.at(-1).slice(0, 7));
    return a === b ? a : `${a} – ${b}`;
  }

  function heightBlock(s) {
    const h = state.height, j = state.hjob?.frame === s.frame ? state.hjob : null;
    let out = `<h2>Tree height</h2>`;
    if (h) {
      const m = heightSummary(h, state.scan);
      out += `<dl class="figures">
        <dt class="big">Typical tree</dt><dd class="big">${m.typical != null ? `about ${m.typical} m` : "–"}</dd>
        <dt>Middle half of cells</dt><dd>${m.lo != null ? `${m.lo}–${m.hi} m` : "–"}</dd>
        <dt>Ground under trees</dt><dd>${m.cover != null ? Math.round(m.cover) + "%" : "–"}</dd>
      </dl><p class="small">${m.inProsopis ? "In the cells mapped as Prosopis." : "Whole area."}
        An AI estimate (Meta/WRI) from satellite photos of ${esc(photoDates(h))}: typically about
        ${h.accuracy?.mae_m || HEIGHT_MAE} m off, not yet checked in Kenya. It counts every tree, not only Prosopis.${h.demo ? " Demo numbers." : ""}</p>`;
    }
    if (j) return out + heightProgress(j);
    if (h) return out;
    if (VIEW) return "";                          // nothing to show, and no button to run it
    const device = canDevice();
    if (!device && state.hmode === "device") state.hmode = "cloud";
    return out + `<p class="small">How tall the trees are, from a free 1 m satellite height map. About a minute.</p>
      ${device ? `<label for="hmode">Run it on</label><select id="hmode" data-act="hmode">
        <option value="cloud" ${state.hmode === "cloud" ? "selected" : ""}>The cloud server (nothing big to download)</option>
        <option value="device" ${state.hmode === "device" ? "selected" : ""}>This device (about 11 MB, can pause)</option>
      </select>` : ""}
      <button class="btn ghost" data-act="height">Estimate tree height</button>`;
  }

  const heightPct = (j) => j.phase === "publishing" ? 95 : Math.round(100 * (j.done || 0) / (j.total || 1));

  function heightProgress(j) {
    const parts = j.mode === "device" && j.total ? ` · ${j.done} of ${j.total} parts` : "";
    const say = {
      sending: "Saved. Sending it to the server…",
      waiting: "Waiting for a server…",
      running: j.mode === "device" ? "Reading the height map" : `On the server: ${j.step || "working"}`,
      publishing: "Publishing the result…",
      paused: "Paused",
      offline: j.mode === "device" ? "No connection, so it paused. It carries on by itself when the connection is back"
        : "No connection right now. The server keeps working; this catches up when you're back",
      failed: `Stopped: ${j.error || "something went wrong"}`,
    }[j.phase] || "Working…";
    const btn = (act, label) => `<button class="link" data-act="${act}">${label}</button>`;
    const pct = heightPct(j);
    return `<div class="progress" role="progressbar" aria-label="Tree height estimate"
        aria-valuemin="0" aria-valuemax="100" aria-valuenow="${pct}"><span style="width:${pct}%"></span></div>
      <p class="small">${esc(say)}${parts}${/[.…]$/.test(say + parts) ? "" : "."}</p><p class="actions">
      ${j.mode === "device" && j.phase === "running" ? btn("hpause", "Pause") : ""}
      ${["paused", "failed", "offline"].includes(j.phase) && (j.mode === "device" || j.phase === "failed") ? btn("hresume", j.phase === "failed" ? "Try again" : "Resume") : ""}
      ${btn("hcancel", "Cancel")}</p>`;
  }

  function startHeight(frame) {
    if (state.hjob) return toast("A tree-height estimate is already running.");
    const mode = state.hmode === "device" && canDevice() ? "device" : "cloud";
    state.hjob = { id: uuid(), frame, mode, at: new Date().toISOString(),
                   phase: mode === "cloud" ? "sending" : "running", done: 0, total: 0 };
    saveHjob();
    render();
    driveHeight();
  }

  function driveHeight() {
    const j = state.hjob;
    if (!j || j.phase === "paused") return;
    if (j.mode === "device") runDevice(j);
    else runCloud(j);
  }

  function pauseHeight() {
    if (state.hjob) { state.hjob.phase = "paused"; saveHjob(); }
    if (deviceCtl) deviceCtl.abort();
    render();
  }

  function resumeHeight() {
    const j = state.hjob;
    if (!j) return;
    j.error = null;
    j.phase = j.mode === "cloud" ? (j.sentAt && j.phase !== "failed" ? "waiting" : "sending") : "running";
    if (j.mode === "cloud" && j.phase === "sending") { j.id = uuid(); j.sentAt = null; }   // a failed run: ask again
    saveHjob();
    render();
    driveHeight();
  }

  function cancelHeight() {
    const j = state.hjob;
    state.hjob = null;
    saveHjob();
    clearTimeout(hTimer);
    if (deviceCtl) deviceCtl.abort();
    if (j?.mode === "device" && window.RizikiHeight) RizikiHeight.clear(j.frame);
    render();
  }

  // After a reload: carry on, unless it was paused by hand.
  function resumeHeightOnStart() {
    const j = state.hjob;
    if (!j) return;
    if (j.mode === "device" && j.phase === "running") j.phase = navigator.onLine ? "running" : "offline";
    driveHeight();
  }

  function heightResult(j, grid, dates) {
    return { frame: j.frame, request_id: j.id, created: new Date().toISOString(), method: j.mode,
      source: HEIGHT_SOURCE, accuracy: { mae_m: HEIGHT_MAE, checked_in_kenya: false },
      image_dates: dates || {}, tree_threshold_m: 1, grid };
  }

  async function runDevice(j) {
    if (deviceCtl) return;                                   // already running
    if (!navigator.onLine) { j.phase = "offline"; saveHjob(); return render(); }
    j.phase = "running";
    saveHjob();
    render();
    const ctl = deviceCtl = new AbortController();
    const onProgress = (d, t) => { j.done = d; j.total = t; saveHjob(); if (state.sel?.frame === j.frame) render(); };
    try {
      let h;
      if (DEMO) h = await demoDevice(j, ctl.signal, onProgress);
      else {
        const b = frameBounds(j.frame);
        const grid = await RizikiHeight.run({ frame: j.frame, bounds: b, cells: 100,
          tileURL: (q) => `${API}/chm?q=${q}`, signal: ctl.signal, onProgress });
        const dates = await RizikiHeight.imageDates(b, (q) => `${API}/chm?meta=${q}`, ctl.signal).catch(() => ({}));
        h = heightResult(j, grid, dates);
      }
      finishHeight(j, h);
    } catch (e) {
      if (state.hjob !== j) return;                          // cancelled
      if (j.phase !== "paused") {
        // A dropped or weak connection shows up as a TypeError from fetch.
        const net = !navigator.onLine || e instanceof TypeError;
        j.phase = net ? "offline" : "failed";
        j.error = net ? null : (e.message || String(e));
        saveHjob();
      }
      render();
    } finally {
      if (deviceCtl === ctl) deviceCtl = null;
    }
  }

  async function runCloud(j) {
    clearTimeout(hTimer);
    if (state.hjob !== j || j.phase === "failed" || j.phase === "paused") return;
    try {
      if (!navigator.onLine) throw new TypeError("offline");
      if (j.phase === "sending") {
        if (DEMO) await sleep(500);
        else {
          await fetchJSON(`${API}/height`, { method: "POST", tries: 2, timeout: 15000,
            body: JSON.stringify({ frame: j.frame, request_id: j.id }) });
        }
        j.sentAt = new Date().toISOString();
        j.phase = "waiting";
      }
      j.offline = false;
      const p = DEMO ? demoProgress(j)
        : await fetchJSON(`${API}/progress?job=height&request_id=${encodeURIComponent(j.id)}`, { tries: 1 });
      if (p) {
        j.done = p.done; j.total = p.total; j.step = p.step;
        j.phase = { waiting: "waiting", running: "running", done: "publishing", failed: "failed" }[p.state] || j.phase;
        if (j.phase === "failed") j.error = "the server run didn't finish";
      }
      if (j.phase === "publishing") {
        // The workflow has pushed; the site takes a minute to show the new file.
        const h = DEMO ? await demoHeight(j)
          : await fetchJSON(`${C.DATA_URL}scans/${j.frame}/height.json?t=${Date.now()}`, { tries: 1 });
        if (h && (DEMO || h.request_id === j.id)) return finishHeight(j, h);
      }
    } catch (e) {
      if (state.hjob !== j) return;
      j.offline = true;
      if (e instanceof SignedOut) j.error = "sign in again to carry on";
    }
    if (state.hjob !== j) return;
    if (j.offline && j.phase !== "sending") j.phase = "offline";
    else if (j.phase === "offline") j.phase = "running";
    saveHjob();
    if (state.sel?.frame === j.frame || !state.sel) render();
    if (j.phase !== "failed") hTimer = setTimeout(() => runCloud(j), DEMO ? 1200 : 10000);
  }

  function finishHeight(j, h) {
    state.hjob = null;
    saveHjob();
    keepHeight(h);
    const fr = state.index?.frames?.[j.frame];
    if (fr) fr.height = Object.keys(h.image_dates || {}).sort().at(-1) || true;
    if (state.sel?.frame === j.frame) state.height = h;
    render();
    toast(`Tree height ready: ${frameName(j.frame)}`);
  }

  // DEMO: the server's steps, played out in about ten seconds.
  function demoProgress(j) {
    const steps = ["Set up job", "Get the code", "Set up Python", "Install", "Estimate tree height", "Publish the result"];
    const k = Math.floor((Date.now() - Date.parse(j.sentAt)) / 1500) - 1;
    if (k < 0) return { state: "waiting", done: 0, total: steps.length, step: "Waiting for a server" };
    if (k >= steps.length) return { state: "done", done: steps.length, total: steps.length, step: "" };
    return { state: "running", done: k, total: steps.length, step: steps[k] };
  }

  // DEMO: the device run, part by part, with Pause and a lost connection behaving as for real.
  async function demoDevice(j, signal, onProgress) {
    const total = 20;
    for (let d = j.done || 0; d < total; d++) {
      onProgress(d, total);
      await sleep(400);
      if (signal.aborted) throw new DOMException("paused", "AbortError");
      if (!navigator.onLine) throw new TypeError("Failed to fetch");
    }
    onProgress(total, total);
    return demoHeight(j);
  }

  // DEMO: a real precomputed estimate if this site has one, else made-up numbers.
  async function demoHeight(j) {
    let h = null;
    try { h = await fetchJSON(`${C.DATA_URL}scans/${j.frame}/height.json`, { tries: 1 }); } catch (e) { /* none */ }
    if (h) return { ...h, request_id: j.id, method: j.mode };
    const n = 100, codes = state.scan?.frame === j.frame ? state.scan.grid?.codes : null;
    let seed = [...j.frame].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 11);
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
    const g = { n, cover: [], tree_m: [], top_m: [] };
    for (let i = 0; i < n * n; i++) {
      const d = codes ? Number(codes[i]) % 9 : Math.floor(rnd() * 4);
      g.cover.push(Math.round(5 + d * 9 + rnd() * 8));
      g.tree_m.push(2 + Math.round(d * 0.7 + rnd() * 2));
      g.top_m.push(3 + Math.round(d * 1.2 + rnd() * 3));
    }
    return { ...heightResult(j, g, { "2018-05-14": 1 }), demo: true };
  }

  // ------------------------------------------------------------------ 11. extras
  function downloadGPX(sc) {
    if (!sc?.plots?.length) return;
    const kind = sc.plots[0].kind === "check" ? "check-points" : "plots";
    const pts = sc.plots.map((p) =>
      `  <wpt lat="${Number(p.lat)}" lon="${Number(p.lon)}"><name>${esc(p.id)} ${esc(p.class)}</name>` +
      `<desc>${p.kind === "check" ? "Check point" : "Plot"} · ${esc(sc.frame)} · ${sc.season} season${sc.demo ? " · DEMO" : ""}</desc></wpt>`).join("\n");
    const xml = `<?xml version="1.0" encoding="UTF-8"?>\n` +
      `<gpx version="1.1" creator="Riziki Prosopis map" xmlns="http://www.topografix.com/GPX/1/1">\n${pts}\n</gpx>\n`;
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([xml], { type: "application/gpx+xml" }));
    a.download = `${sc.frame}_${sc.season}_${kind}${sc.demo ? "_DEMO" : ""}.gpx`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  function locate() {
    if (!navigator.geolocation) return toast("This device can't share its location.");
    toast("Finding your location…", 8000);
    navigator.geolocation.getCurrentPosition((p) => {
      const ll = [p.coords.latitude, p.coords.longitude];
      if (meDot) map.removeLayer(meDot);
      meDot = L.circleMarker(ll, { radius: 8, color: "#fff", weight: 3, fillColor: "#2b6cb0", fillOpacity: 1 }).addTo(map);
      const id = frameId(ll[0], ll[1]);
      if (inRegion(id)) { map.setView(ll, 12); selectFrame(id); toast("You are in the outlined square."); }
      else { map.setView(ll, 11); toast("You're outside the area this map covers."); }
    }, () => toast("Couldn't get your location. Check that location is allowed for this site."),
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }

  // DEMO mode: scans finished in the browser are added to the index.
  function mergeDemoScans() {
    for (const k of store.keys("demo:")) {
      const sc = store.get(k, null);
      if (sc) addToIndex(sc);
    }
  }

  // DEMO mode: a made-up scan, so the whole flow can be tried without a server.
  function demoScan(j) {
    const n = 100;
    let seed = [...(j.frame + j.season)].reduce((a, ch) => (a * 31 + ch.charCodeAt(0)) >>> 0, 7);
    const rnd = () => ((seed = (seed * 1664525 + 1013904223) >>> 0) / 4294967296);
    const centre = 25 + rnd() * 50, width = 7 + rnd() * 8, cells = { 0: [], 1: [], 2: [], 3: [] };
    let codes = "";
    for (let r = 0; r < n; r++) {
      const river = centre + 16 * Math.sin(r / 12);
      for (let c = 0; c < n; c++) {
        const v = Math.exp(-Math.abs(c - river) / width) + (rnd() - 0.5) * 0.25;
        const ch = v > 0.9 ? "3" : v > 0.6 ? "2" : v > 0.33 ? "1" : "0";
        codes += ch;
        cells[ch].push(r * n + c);
      }
    }
    const b = frameBounds(j.frame);
    const ha = 111.32 * Math.cos(((b.s + b.n) / 2) * Math.PI / 180) * 110.57 * C.FRAME_DEG ** 2 / (n * n) * 100;
    const total = (cells[1].length + cells[2].length + cells[3].length) * ha;
    const plots = [];
    for (const [ch, name] of [["3", "thick"], ["2", "medium"], ["1", "thin"], ["0", "none"]]) {
      for (let k = 0; k < 7 && cells[ch].length; k++) {
        const i = cells[ch][Math.floor(rnd() * cells[ch].length)];
        plots.push({ id: `C${pad(plots.length + 1, 2)}`, class: name, kind: "check",
          lat: +(b.n - (Math.floor(i / n) + 0.5) * C.FRAME_DEG / n).toFixed(5),
          lon: +(b.w + ((i % n) + 0.5) * C.FRAME_DEG / n).toFixed(5) });
      }
    }
    return {
      demo: true, request_id: j.id, frame: j.frame, season: j.season, created: new Date().toISOString(),
      images: { sensor: "Sentinel-2 L2A", dry: [`${j.season}-01-08`, `${j.season}-03-26`],
        wet: [`${j.season}-04-10`, `${j.season}-08-28`], to: `${j.season}-08-28`, n_scenes: 12 },
      model: { id: "prosopis_s2_v1", trained_on: "Baringo, Eschen et al. 2021 labels (2016)" },
      applicability: 0.9, status: "scout", status_reasons: ["This region has not been checked on the ground yet."],
      cell_ha: ha, area_ha: { prosopis: total, low: total * 0.86, high: total * 1.14 },
      density_ha: { thick: cells[3].length * ha, medium: cells[2].length * ha, thin: cells[1].length * ha },
      grid: { n, codes }, plots,
    };
  }

  // ------------------------------------------------------------------- 12. start
  async function start() {
    if (C.LOCKED) {
      await RizikiLock.ready(C.DATA_URL);       // asks for the password unless this device has the key
      const b = $("lockbtn");
      b.hidden = false;
      b.addEventListener("click", () => RizikiLock.forget());
    }
    initMap();
    wirePanel();
    setBanner();
    setNet(navigator.onLine ? "online" : "offline");
    if (C.LOGOUT_URL) { const a = $("logout"); a.href = C.LOGOUT_URL; a.hidden = false; }
    if (DEMO) mergeDemoScans();
    drawFrames();

    const want = readHash() || store.get("sel", null);
    if (want && frameBounds(want.frame)) selectFrame(want.frame, want.season, want.compare);
    else render();

    loadIndex();
    if (!VIEW) {
      flushOutbox();
      schedulePoll(3000);
      setInterval(() => { if (state.outbox.some((j) => !j.sent)) flushOutbox(); }, 30000);
      resumeHeightOnStart();
      setInterval(() => { if (state.hjob?.phase === "offline" && navigator.onLine) driveHeight(); }, 30000);
    }

    window.addEventListener("online", () => { setNet("online"); flushOutbox(); loadIndex(); if (state.hjob?.phase === "offline") driveHeight(); });
    window.addEventListener("offline", () => { setNet("offline"); render(); });
    document.addEventListener("visibilitychange", () => { if (!document.hidden && state.outbox.length) pollOnce(); });
    window.addEventListener("hashchange", () => {
      const h = readHash();
      if (h) selectFrame(h.frame, h.season, h.compare);
    });

    try { navigator.serviceWorker.register("sw.js").catch(() => { /* still works online */ }); } catch (e) { /* no service worker here */ }
  }

  // Exposed for testing in the browser console only.
  window.__riziki = { frameId, frameBounds, compareScans, heightSummary, state };
  start();
})();
