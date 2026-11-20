// GET /api/progress?job=height&request_id=...  ->  {state, done, total, step}
// state: waiting | running | done | failed. Feeds the dashboard's progress bar.

import { REQUEST_ID, WORKFLOWS, reply, progress } from "../../lib/github.js";

export async function onRequestGet({ request, env }) {
  const q = new URL(request.url).searchParams;
  const job = q.get("job") || "";
  const requestId = q.get("request_id") || "";
  if (!WORKFLOWS[job]) return reply({ error: "job must be scan or height" }, 400);
  if (!REQUEST_ID.test(requestId)) return reply({ error: "bad request_id" }, 400);
  return progress(env, job, requestId);
}

export async function onRequest() {
  return reply({ error: "use GET" }, 405);
}
