// POST /api/scan  {frame, season, request_id}  ->  starts .github/workflows/scan.yml
//
// A Cloudflare Pages Function: it lives on the same site as the dashboard, so
// Cloudflare Access (the email-code sign-in) protects it too. It exists for
// one reason: the GitHub token that starts a scan must never be in the page.
// Settings: see lib/github.js. Optional: FIRST_SEASON (default 2016).

import { FRAME, REQUEST_ID, reply, dispatch } from "../../lib/github.js";

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch (e) { return reply({ error: "send JSON" }, 400); }

  // Check everything here, so nothing odd ever reaches the workflow.
  const frame = String(body.frame || "");
  const season = Number(body.season);
  const requestId = String(body.request_id || "");
  const thisYear = new Date().getUTCFullYear();
  if (!FRAME.test(frame)) return reply({ error: "frame must look like N0040_E03600" }, 400);
  if (!Number.isInteger(season) || season < Number(env.FIRST_SEASON || 2016) || season > thisYear) {
    return reply({ error: "season out of range" }, 400);
  }
  if (!REQUEST_ID.test(requestId)) return reply({ error: "bad request_id" }, 400);
  return dispatch(env, "scan", { frame, season: String(season), request_id: requestId });
}

export async function onRequest() {
  return reply({ error: "use POST" }, 405);
}
