// Settings for the Prosopis map. Edit these; nothing else needs changing.
window.RIZIKI_CONFIG = {
  // Where index.json and scans/ live. Relative to this page, or a full URL.
  DATA_URL: "data/",

  // The API on the same site (functions/api/: scan, height, progress, chm): set to "/api".
  // Leave empty for DEMO mode: scan and tree-height requests are simulated in the browser.
  API_URL: "",

  // true: show the stored maps only. No scan or tree-height buttons, no DEMO banner.
  // For a plain static host such as GitHub Pages. tools/build_site.py sets this.
  VIEW_ONLY: false,

  // true: data/ is encrypted and the page asks for the password (data/lock.json).
  // Set by tools/build_site.py; don't set it by hand.
  LOCKED: false,

  // Frames are only offered inside this box (degrees).
  REGION: { south: -5.0, west: 33.5, north: 5.5, east: 42.0 },

  // Fixed frame grid. Must match FRAME_DEG in the Python scanner.
  FRAME_DEG: 0.1,

  // Frame squares appear from this zoom level (10 = about 11 km per 70 px).
  MIN_FRAME_ZOOM: 10,

  // Oldest season offered in the season list (first full Sentinel-2 year).
  OLDEST_SEASON: 2016,

  // Shown as a "Sign out" link if set. Behind Cloudflare Access use:
  // "/cdn-cgi/access/logout"
  LOGOUT_URL: "",

  BASEMAP: {
    url: "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    maxZoom: 17,
    // maxNativeZoom: 13,  // optional: stretch tiles past this zoom (for a local tile folder)
  },
};
