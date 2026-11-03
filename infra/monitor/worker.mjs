// External clock/watchdog: no Firebase/channel secrets and no enable API path.
const workflows = ["alert-engine.yml", "alert-test-push.yml"];
const isSafetyPath = (path = "") => path.startsWith("alert_engine/") || [
  "requirements.txt", ".github/workflows/alert-engine.yml", ".github/workflows/alert-test-push.yml",
].includes(path);

function configuration(env) {
  let pingConfigured = false;
  try { const url = new URL(env.HEALTHCHECK_PING_URL); pingConfigured = url.protocol === "https:" && !url.username && !url.password; } catch { /* no URL/secret logging */ }
  return { githubToken: Boolean(env.GITHUB_ACTIONS_TOKEN), healthchecks: pingConfigured,
    safeCommit: /^[a-f0-9]{40}$/i.test(env.MIN_SAFE_COMMIT ?? "") };
}

async function github(env, path, method = "GET", body, fetcher = fetch) {
  const response = await fetcher(`https://api.github.com/repos/cococute88/goralert/${path}`, {
    method, headers: { Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "goralert-watchdog", "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`github_${response.status}`);
  return response.status === 204 ? null : response.json();
}

export async function tick(env, now = new Date(), fetcher = fetch) {
  // A fresh check stays New/Paused until cutover. Never emit a fake success or
  // failure during setup, even if secrets are missing or the clock is misarmed.
  // A previously active check still detects missing pings independently.
  if (env.MONITORING_ACTIVE !== "true") return { mode: "setup", healthy: null, problems: [] };
  const configured = configuration(env);
  if (!configured.healthchecks) throw new Error("watchdog_not_configured");
  const problems = [];
  let completed = false;
  try {
    if (!configured.githubToken || !configured.safeCommit) throw new Error("watchdog_not_configured");
    if (env.RECOVERY_ARMED !== "approved-v1") throw new Error("clock_disarmed");
    const states = await Promise.all(workflows.map((file) => github(env, `actions/workflows/${file}`, "GET", undefined, fetcher)));
    if (states.some((wf) => wf.state !== "active")) problems.push("workflow_disabled");
    else {
      // Require reviewed protections to remain unchanged, including descendant
      // revert commits. Update MIN_SAFE_COMMIT only after reviewing new engine
      // changes. UI/docs commits can proceed without re-arming.
      const comparison = await github(env, `compare/${encodeURIComponent(env.MIN_SAFE_COMMIT)}...main`, "GET", undefined, fetcher);
      if (!["ahead", "identical"].includes(comparison.status)) throw new Error("unsafe_main_revision");
      if (!Array.isArray(comparison.files) || comparison.files.length >= 300 || comparison.files.some((file) => isSafetyPath(file.filename) || isSafetyPath(file.previous_filename))) throw new Error("protection_code_changed");
      const healthRes = await fetcher("https://goralert.vercel.app/api/engine-health", { signal: AbortSignal.timeout(10_000), headers: { "Cache-Control": "no-cache" } });
      const health = healthRes.ok ? await healthRes.json() : { state: "unknown" };
      // Missing/stale heartbeat may bootstrap; unreadable Firestore, wrong
      // target, missing server config or an old endpoint cannot authorize I/O.
      if (health.dispatchReady !== true || !["healthy", "delayed", "stopped", "unknown"].includes(health.state)) throw new Error("health_preflight_failed");
      if (health.state !== "healthy") problems.push(`engine_${health.state ?? "unknown"}`);
      // A stale initial heartbeat may recover only under the explicit arm gate.
      // Every engine run applies durable backlog protection before any delivery.
      if (now.getUTCMinutes() % 30 === 3) {
        await github(env, "actions/workflows/alert-engine.yml/dispatches", "POST", { ref: "main", inputs: { "job-scope": "all", "dry-run": false } }, fetcher);
      }
      await github(env, "actions/workflows/alert-test-push.yml/dispatches", "POST", { ref: "main", inputs: { uid: "" } }, fetcher);
      const runs = await github(env, "actions/workflows/alert-test-push.yml/runs?per_page=5", "GET", undefined, fetcher);
      const last = runs.workflow_runs?.find((run) => run.status === "completed");
      if (!last || last.conclusion !== "success" || now.getTime() - Date.parse(last.updated_at) > 45 * 60_000) problems.push("test_drain_failed_or_stale");
    }
    completed = true;
  } catch (error) {
    // Codes only; never stringify credential-bearing request objects/URLs.
    const code = error?.message;
    problems.push(/^github_\d{3}$/.test(code ?? "") || ["clock_disarmed", "watchdog_not_configured", "health_preflight_failed", "unsafe_main_revision", "protection_code_changed"].includes(code) ? code : "watchdog_check_failed");
  }
  // Healthchecks sends email independently. A dead Worker also expires its
  // deadline because no success pings arrive; engine failures send /fail.
  const ping = env.HEALTHCHECK_PING_URL.replace(/\/$/, "") + (completed && problems.length === 0 ? "" : "/fail");
  try {
    const response = await fetcher(ping, { method: "POST", body: problems.join(","), signal: AbortSignal.timeout(10_000) });
    if (!response.ok) throw new Error();
  } catch { throw new Error("watchdog_ping_failed"); }
  if (problems.length) console.warn(JSON.stringify({ watchdog: "unhealthy", reasons: problems }));
  return { mode: "active", healthy: completed && problems.length === 0, problems };
}

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(tick(env, new Date(event.scheduledTime))); },
  async fetch(request, env) {
    // No external request, dispatch or ping on visiting the GUI status URL.
    return Response.json({ mode: env.MONITORING_ACTIVE === "true" ? "active" : "setup", healthy: null,
      clockArmed: env.RECOVERY_ARMED === "approved-v1", configured: configuration(env) }, { headers: { "Cache-Control": "no-store" } });
  },
};
