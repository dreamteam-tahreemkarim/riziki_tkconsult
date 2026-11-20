// Shared by the functions in functions/api/. Talks to the GitHub Actions API
// with the token kept in Cloudflare (never in the page).
//
// Set in Cloudflare Pages > Settings > Environment variables:
//   GITHUB_TOKEN   fine-grained token, this repo only, "Actions: read and write"
//   GITHUB_REPO    dreamteam-tahreemkarim/riziki_tkconsult
//   GITHUB_REF     main            (optional)

export const FRAME = /^[NS]\d{4}_[EW]\d{5}$/;
export const REQUEST_ID = /^[A-Za-z0-9-]{8,64}$/;
export const WORKFLOWS = { scan: "scan.yml", height: "height.yml" };

export const reply = (body, status = 200) =>
  new Response(JSON.stringify(body), {
    status, headers: { "Content-Type": "application/json", "Cache-Control": "no-store" },
  });

function gh(env, path, init = {}) {
  return fetch(`https://api.github.com/repos/${env.GITHUB_REPO}${path}`, {
    ...init,
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "riziki-api",
      ...(init.headers || {}),
    },
  });
}

// Start a workflow. inputs are strings; they were checked by the caller.
export async function dispatch(env, job, inputs) {
  const r = await gh(env, `/actions/workflows/${WORKFLOWS[job]}/dispatches`, {
    method: "POST",
    body: JSON.stringify({ ref: env.GITHUB_REF || "main", inputs }),
  });
  return r.ok ? reply({ ok: true, job, ...inputs }, 202) : reply({ error: `GitHub answered ${r.status}` }, 502);
}

// How far a run has got. Each workflow puts request_id in its run name, so the
// run is found by name; its finished steps / all steps is the progress.
export async function progress(env, job, requestId) {
  const runs = await gh(env, `/actions/workflows/${WORKFLOWS[job]}/runs?event=workflow_dispatch&per_page=30`);
  if (!runs.ok) return reply({ error: `GitHub answered ${runs.status}` }, 502);
  const run = (await runs.json()).workflow_runs.find((r) => (r.display_title || "").includes(requestId));
  if (!run) return reply({ state: "waiting", done: 0, total: 1, step: "Waiting for a server" });
  const jobs = await gh(env, `/actions/runs/${run.id}/jobs`);
  const steps = jobs.ok ? ((await jobs.json()).jobs[0]?.steps || []) : [];
  const done = steps.filter((s) => s.status === "completed").length;
  const now = steps.find((s) => s.status === "in_progress");
  const state = run.status !== "completed" ? (run.status === "queued" ? "waiting" : "running")
    : run.conclusion === "success" ? "done" : "failed";
  return reply({ state, done, total: Math.max(steps.length, 1), step: now?.name || (state === "waiting" ? "Waiting for a server" : "") });
}
