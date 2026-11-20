// POST /api/height  {frame, request_id}  ->  starts .github/workflows/height.yml
// ("Estimate tree height", run on the cloud server). Settings: see lib/github.js.

import { FRAME, REQUEST_ID, reply, dispatch } from "../../lib/github.js";

export async function onRequestPost({ request, env }) {
  let body;
  try { body = await request.json(); } catch (e) { return reply({ error: "send JSON" }, 400); }
  const frame = String(body.frame || "");
  const requestId = String(body.request_id || "");
  if (!FRAME.test(frame)) return reply({ error: "frame must look like N0040_E03600" }, 400);
  if (!REQUEST_ID.test(requestId)) return reply({ error: "bad request_id" }, 400);
  return dispatch(env, "height", { frame, request_id: requestId });
}

export async function onRequest() {
  return reply({ error: "use POST" }, 405);
}
