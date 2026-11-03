import assert from "node:assert/strict";
import test from "node:test";
import worker, { tick } from "../infra/monitor/worker.mjs";

const at = new Date("2026-11-03T03:03:00Z");
function fixture(options = {}) {
  const calls = [];
  const env = { MONITORING_ACTIVE: "true", RECOVERY_ARMED: "approved-v1", MIN_SAFE_COMMIT: "a".repeat(40),
    GITHUB_ACTIONS_TOKEN: "offline", HEALTHCHECK_PING_URL: "https://offline.test/ping", ...options.env };
  const fetcher = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.startsWith("https://offline.test")) {
      if (options.pingThrows) throw new Error("offline secret must not be logged");
      return new Response(null, { status: options.pingStatus ?? 200 });
    }
    if (url.includes("api.github.com")) {
      if (options.githubThrows) throw new Error("offline secret must not be logged");
      if (options.githubStatus) return new Response(null, { status: options.githubStatus });
      if (url.includes("compare/")) return Response.json({ status: "identical", files: options.files ?? [] });
      if (url.includes("/runs?")) return Response.json({ workflow_runs: [{ status: "completed", conclusion: "success", updated_at: at.toISOString() }] });
      if (url.endsWith("dispatches")) return new Response(null, { status: 204 });
      return Response.json({ state: options.workflowState ?? "active" });
    }
    return Response.json(options.health ?? { state: "healthy", dispatchReady: true });
  };
  return { env, calls, fetcher };
}
const dispatches = (calls) => calls.filter(([url]) => url.endsWith("dispatches"));

test("setup without secrets performs zero dispatches and zero Healthchecks pings", async () => {
  const calls = [];
  const result = await tick({ RECOVERY_ARMED: "false" }, at, async (...args) => { calls.push(args); throw new Error("unexpected outbound call"); });
  assert.equal(result.mode, "setup");
  assert.equal(result.healthy, null);
  assert.equal(calls.length, 0);
});
test("arming the clock cannot bypass the separate monitoring activation gate", async () => {
  const f = fixture({ env: { MONITORING_ACTIVE: "false" } });
  assert.equal((await tick(f.env, at, f.fetcher)).mode, "setup");
  assert.equal(f.calls.length, 0);
});
test("an active monitor reports a disarmed clock instead of hiding the outage", async () => {
  const f = fixture({ env: { RECOVERY_ARMED: "false" } });
  assert.equal((await tick(f.env, at, f.fetcher)).healthy, false);
  assert.equal(dispatches(f.calls).length, 0);
  assert.equal(f.calls.at(-1)[0], "https://offline.test/ping/fail");
});
for (const state of ["unknown", "stopped", "delayed"]) {
  test(`approved bootstrap can run with ${state} heartbeat, without claiming health`, async () => {
    const f = fixture({ health: { state, dispatchReady: true } });
    assert.equal((await tick(f.env, at, f.fetcher)).healthy, false);
    assert.equal(dispatches(f.calls).length, 2);
    assert.equal(f.calls.at(-1)[0], "https://offline.test/ping/fail");
    const recovered = fixture();
    assert.equal((await tick(recovered.env, at, recovered.fetcher)).healthy, true);
  });
}
for (const health of [{ state: "unknown", dispatchReady: false }, { state: "healthy" }]) {
  test(`permission/configuration or old endpoint cannot authorize dispatch: ${JSON.stringify(health)}`, async () => {
    const f = fixture({ health });
    assert.equal((await tick(f.env, at, f.fetcher)).healthy, false);
    assert.equal(dispatches(f.calls).length, 0);
    assert.equal(f.calls.at(-1)[0], "https://offline.test/ping/fail");
  });
}
for (const status of [401, 403, 429, 500]) {
  test(`active monitoring reports GitHub HTTP ${status} and sends no jobs`, async () => {
    const f = fixture({ githubStatus: status });
    const result = await tick(f.env, at, f.fetcher);
    assert.equal(result.healthy, false);
    assert.ok(result.problems.includes(`github_${status}`));
    assert.equal(dispatches(f.calls).length, 0);
  });
}
test("network/Cloudflare execution failure is not a success ping", async () => {
  const f = fixture({ githubThrows: true });
  const result = await tick(f.env, at, f.fetcher);
  assert.equal(result.healthy, false);
  assert.doesNotMatch(JSON.stringify(result), /offline secret/);
  assert.equal(f.calls.at(-1)[0], "https://offline.test/ping/fail");
});
for (const options of [{ pingStatus: 503 }, { pingThrows: true }]) {
  test(`Healthchecks failure rejects the cron task: ${JSON.stringify(options)}`, async () => {
    const f = fixture(options);
    await assert.rejects(tick(f.env, at, f.fetcher), /watchdog_ping_failed/);
  });
}
for (const filename of ["alert_engine/delivery.py", "alert_engine/recurrence.py", "alert_engine/channels/push.py", "alert_engine/models.py"]) {
  test(`unreviewed engine dependency blocks dispatch: ${filename}`, async () => {
    const f = fixture({ files: [{ filename }] });
    assert.equal((await tick(f.env, at, f.fetcher)).healthy, false);
    assert.equal(dispatches(f.calls).length, 0);
  });
}
test("invalid safe commit fails closed and remains observable", async () => {
  const f = fixture({ env: { MIN_SAFE_COMMIT: "main" } });
  assert.equal((await tick(f.env, at, f.fetcher)).healthy, false);
  assert.equal(dispatches(f.calls).length, 0);
  assert.equal(f.calls.at(-1)[0], "https://offline.test/ping/fail");
});
test("GUI Worker status is setup evidence only and exposes no secret values", async () => {
  const f = fixture({ env: { MONITORING_ACTIVE: "false" } });
  const response = await worker.fetch(new Request("https://offline.test"), f.env);
  const body = await response.json();
  assert.equal(body.mode, "setup");
  assert.equal(body.healthy, null);
  assert.doesNotMatch(JSON.stringify(body), /offline|aaaaaaa/);
});
test("scheduled handler preserves Healthchecks failure for Cloudflare execution reporting", async () => {
  const f = fixture({ pingStatus: 503 });
  const original = globalThis.fetch;
  globalThis.fetch = f.fetcher;
  let task;
  try {
    await worker.scheduled({ scheduledTime: at.getTime() }, f.env, { waitUntil(value) { task = value; } });
    await assert.rejects(task, /watchdog_ping_failed/);
  } finally { globalThis.fetch = original; }
});
