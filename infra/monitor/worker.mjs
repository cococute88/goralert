// External clock/watchdog: no Firebase/channel secrets and no enable API path.
const workflows = ["alert-engine.yml", "alert-test-push.yml"];
const safetyPaths = new Set([
  "alert_engine/backlog.py", "alert_engine/reliability_store.py", "alert_engine/engine.py",
  "alert_engine/firestore_client.py", "alert_engine/test_push.py", "alert_engine/config.py",
  "alert_engine/main.py", ".github/workflows/alert-engine.yml", ".github/workflows/alert-test-push.yml",
]);

async function github(env, path, method = "GET", body, fetcher = fetch) {
  const response = await fetcher(`https://api.github.com/repos/cococute88/goralert/${path}`, {
    method, headers: { Authorization: `Bearer ${env.GITHUB_ACTIONS_TOKEN}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "goralert-watchdog", "Content-Type": "application/json" },
    ...(body ? { body: JSON.stringify(body) } : {}), signal: AbortSignal.timeout(10_000),
  });
  if (!response.ok) throw new Error(`github_${response.status}`);
  return response.status === 204 ? null : response.json();
}

export async function tick(env, now = new Date(), fetcher = fetch) {
  if (!env.HEALTHCHECK_PING_URL || !env.GITHUB_ACTIONS_TOKEN || !env.MIN_SAFE_COMMIT) throw new Error("watchdog_not_configured");
  const problems = [];
  let completed = false;
  try {
    const states = await Promise.all(workflows.map((file) => github(env, `actions/workflows/${file}`, "GET", undefined, fetcher)));
    if (states.some((wf) => wf.state !== "active")) problems.push("workflow_disabled");
    else if (env.RECOVERY_ARMED !== "approved-v1") problems.push("cutover_not_approved");
    else {
      // Require reviewed protections to remain unchanged, including descendant
      // revert commits. Update MIN_SAFE_COMMIT only after reviewing new engine
      // changes. UI/docs commits can proceed without re-arming.
      const comparison = await github(env, `compare/${encodeURIComponent(env.MIN_SAFE_COMMIT)}...main`, "GET", undefined, fetcher);
      if (!["ahead", "identical"].includes(comparison.status)) throw new Error("unsafe_main_revision");
      if (!Array.isArray(comparison.files) || comparison.files.length >= 300 || comparison.files.some((file) => safetyPaths.has(file.filename) || safetyPaths.has(file.previous_filename))) throw new Error("protection_code_changed");
      const healthRes = await fetcher("https://goralert.vercel.app/api/engine-health", { signal: AbortSignal.timeout(10_000), headers: { "Cache-Control": "no-cache" } });
      const health = healthRes.ok ? await healthRes.json() : { state: "unknown" };
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
    problems.push(error?.message?.startsWith("github_") ? error.message : "watchdog_check_failed");
  }
  // Healthchecks sends email independently. A dead Worker also expires its
  // deadline because no success pings arrive; engine failures send /fail.
  const ping = env.HEALTHCHECK_PING_URL.replace(/\/$/, "") + (completed && problems.length === 0 ? "" : "/fail");
  const response = await fetcher(ping, { method: "POST", body: problems.join(","), signal: AbortSignal.timeout(10_000) });
  if (!response.ok) throw new Error("watchdog_ping_failed");
  if (problems.length) console.warn(JSON.stringify({ watchdog: "unhealthy", reasons: problems }));
  return { healthy: completed && problems.length === 0, problems };
}

export default {
  async scheduled(event, env, ctx) { ctx.waitUntil(tick(env, new Date(event.scheduledTime))); },
  async fetch() { return new Response("Goralert watchdog; inspect cron and independent dead-man check."); },
};
