import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import { createRequire } from "node:module";
import ts from "typescript";
import { tick } from "../infra/monitor/worker.mjs";

const require = createRequire(import.meta.url);
function load(filename, replacements = {}) {
  const output = ts.transpileModule(fs.readFileSync(filename, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  }).outputText;
  const module = { exports: {} };
  Function("exports", "module", "require", output)(module.exports, module, (id) => {
    if (replacements[id]) return replacements[id];
    if (id.startsWith("@/")) return load(id.replace("@/", "") + ".ts");
    return require(id);
  });
  return module.exports;
}

const { classifyEngineHealth } = load("lib/alerts/engine-health.ts");
const now = Date.parse("2026-11-03T03:00:00Z");
const active = { "alert-engine.yml": "active", "alert-test-push.yml": "active" };
const React = require("react");
const { renderToStaticMarkup } = require("react-dom/server");
const progress = load("components/alerts/TestPushProgress.tsx", {
  "@/components/alerts/ui": { cx: (...parts) => parts.filter(Boolean).join(" ") },
});
test("done metadata alone and missing channel results cannot confirm success", () => {
  const { confirmedTestDelivery } = load("lib/alerts/test-push.ts");
  assert.equal(confirmedTestDelivery(["push"], []), false);
  assert.equal(confirmedTestDelivery(["push", "telegram"], [{ channel: "push", status: "sent" }]), false);
  assert.equal(confirmedTestDelivery(["push"], [{ channel: "push", status: "unknown" }]), false);
  assert.equal(confirmedTestDelivery(["push"], [{ channel: "push", status: "sent" }]), true);
});
test("204 and timeout never mark engine execution confirmed", () => {
  for (const kind of ["dispatching", "waiting", "timeout"]) {
    assert.equal(progress.computeStepStatuses({ kind })[2], "pending");
  }
  const html = renderToStaticMarkup(React.createElement(progress.TestPushProgress, { phase: { kind: "timeout" } }));
  assert.match(html, /실행 여부와 발송 결과를 확인하지 못했습니다/);
  assert.doesNotMatch(html, /엔진이 실행 중입니다/);
});
test("unknown delivery and partial success are distinct from success", () => {
  const unknown = renderToStaticMarkup(React.createElement(progress.TestPushProgress, { phase: { kind: "unknown" } }));
  const partial = renderToStaticMarkup(React.createElement(progress.TestPushProgress, { phase: { kind: "partial", channels: "Telegram" } }));
  assert.match(unknown, /자동 재발송하지 않습니다/);
  assert.match(partial, /일부 성공/);
  assert.notEqual(progress.computeStepStatuses({ kind: "unknown" })[4], "done");
});

function waiter(onSnapshot) {
  const source = fs.readFileSync("lib/alerts/repositories.ts", "utf8");
  const functionSource = source.slice(source.indexOf("export function waitForTestPushResult("));
  const output = ts.transpileModule(functionSource, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  Function("exports", "module", "requireDb", "onSnapshot", "testPushRequestDoc", output)(module.exports, module, () => ({}), onSnapshot, () => ({}));
  return module.exports.waitForTestPushResult;
}
test("queue observation waits for server terminal state and reports real progress", async () => {
  let emit;
  let unsubscribed = false;
  const wait = waiter((ref, options, callback) => {
    assert.equal(options.includeMetadataChanges, true);
    emit = callback;
    return () => { unsubscribed = true; };
  });
  const observed = [];
  const result = wait("u", "r", 1000, (value) => observed.push(value.status));
  const snapshot = (status, fromCache = false) => ({ data: () => ({ status, results: [] }), metadata: { fromCache, hasPendingWrites: false } });
  emit(snapshot("done", true)); // cached terminal data cannot confirm delivery
  emit(snapshot("processing"));
  assert.deepEqual(observed, ["processing"]);
  assert.equal(unsubscribed, false);
  emit(snapshot("partial"));
  assert.equal((await result).status, "partial");
  assert.equal(unsubscribed, true);
});
test("queue timeout/read failure resolves unknown rather than successful delivery", async () => {
  const timeoutWait = waiter(() => () => {});
  assert.equal(await timeoutWait("u", "r", 1), null);
  const errorWait = waiter((ref, options, callback, onError) => { queueMicrotask(onError); return () => {}; });
  assert.equal(await errorWait("u", "r", 1000), null);
});
for (const [minutes, state] of [[30, "healthy"], [80, "delayed"], [181, "stopped"]]) {
  test(`heartbeat age ${minutes}min -> ${state}`, () => {
    assert.equal(classifyEngineHealth({ lastSuccessfulRunAt: new Date(now - minutes * 60_000).toISOString() }, active, now).state, state);
  });
}
test("missing heartbeat/read permissions never imply health", () => {
  assert.equal(classifyEngineHealth(null, active, now).state, "unknown");
  assert.equal(classifyEngineHealth({ lastSuccessfulRunAt: new Date(now).toISOString() }, { engine: "unknown" }, now).state, "unknown");
  assert.equal(classifyEngineHealth({ lastSuccessfulRunAt: "bad" }, active, now).state, "unknown");
});
for (const state of ["disabled_inactivity", "disabled_manually"]) {
  test(`workflow ${state} overrides fresh heartbeat`, () => {
    assert.equal(classifyEngineHealth({ lastSuccessfulRunAt: new Date(now).toISOString() }, { engine: state }, now).state, "stopped");
  });
}
test("evaluation and delivery failures degrade recent runs", () => {
  for (const field of ["evaluationErrors", "deliveryErrors"]) assert.equal(classifyEngineHealth({ lastSuccessfulRunAt: new Date(now).toISOString(), [field]: 1 }, active, now).state, "delayed");
});

for (const [state, dispatchStatus, expectedStatus] of [
  ["active", 204, 202], ["disabled_inactivity", 204, 409], ["disabled_manually", 204, 409],
  ["active", 422, 502], ["active", 401, 502], ["active", 403, 502], ["unknown", 204, 409],
]) {
  test(`dispatch route ${state}, HTTP ${dispatchStatus}`, async () => {
    const before = globalThis.fetch;
    process.env.GITHUB_DISPATCH_TOKEN = "offline-test-token";
    process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "offline-key";
    const calls = [];
    globalThis.fetch = async (url, options) => {
      calls.push([url, options]);
      if (url.includes("identitytoolkit")) return Response.json({ users: [{ localId: "offline-user" }] });
      if (url.endsWith("dispatches")) return new Response(null, { status: dispatchStatus });
      return Response.json({ state });
    };
    try {
      const route = load("app/api/test-push/route.ts");
      const response = await route.POST(new Request("http://localhost/api/test-push", { method: "POST", headers: { Authorization: "Bearer offline" } }));
      const body = await response.json();
      assert.equal(response.status, expectedStatus);
      assert.equal(body.ok, expectedStatus === 202);
      assert.equal(body.fallbackAvailable, false);
      assert.equal(calls.filter(([url]) => url.endsWith("dispatches")).length, state === "active" ? 1 : 0);
      assert.doesNotMatch(JSON.stringify(body), /offline-test-token|Cannot trigger/);
      if (body.ok) assert.equal(body.accepted, true); // acceptance only, never 'sent'
    } finally { globalThis.fetch = before; delete process.env.GITHUB_DISPATCH_TOKEN; delete process.env.NEXT_PUBLIC_FIREBASE_API_KEY; }
  });
}
test("dispatch rejects missing Firebase authentication without external calls", async () => {
  const route = load("app/api/test-push/route.ts");
  assert.equal((await route.POST(new Request("http://localhost", { method: "POST" }))).status, 401);
});
test("workflow state authentication failure prevents dispatch", async () => {
  const original = globalThis.fetch;
  process.env.GITHUB_DISPATCH_TOKEN = "offline";
  process.env.NEXT_PUBLIC_FIREBASE_API_KEY = "offline";
  let dispatched = false;
  globalThis.fetch = async (url) => {
    if (url.includes("identitytoolkit")) return Response.json({ users: [{ localId: "offline" }] });
    if (url.endsWith("dispatches")) dispatched = true;
    return new Response(null, { status: 401 });
  };
  try {
    const res = await load("app/api/test-push/route.ts").POST(new Request("http://localhost", { method: "POST", headers: { authorization: "Bearer offline" } }));
    assert.equal((await res.json()).code, "github_auth");
    assert.equal(dispatched, false);
  } finally { globalThis.fetch = original; delete process.env.GITHUB_DISPATCH_TOKEN; delete process.env.NEXT_PUBLIC_FIREBASE_API_KEY; }
});
test("health endpoint read failure cannot display a healthy engine", async () => {
  const original = globalThis.fetch;
  process.env.GITHUB_DISPATCH_TOKEN = "offline";
  process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID = "offline-project";
  globalThis.fetch = async (url) => url.includes("firestore.googleapis") ? new Response(null, { status: 403 }) : Response.json({ state: "active" });
  try {
    const res = await load("app/api/engine-health/route.ts").GET();
    assert.equal((await res.json()).state, "unknown");
  } finally { globalThis.fetch = original; delete process.env.GITHUB_DISPATCH_TOKEN; delete process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID; }
});

function monitorFixture(options = {}) {
  const calls = [];
  const env = { MONITORING_ACTIVE: "true", GITHUB_ACTIONS_TOKEN: "offline", HEALTHCHECK_PING_URL: "https://offline.test/ping", MIN_SAFE_COMMIT: "a".repeat(40), RECOVERY_ARMED: "approved-v1", ...options.env };
  const fetcher = async (url, init = {}) => {
    calls.push([url, init]);
    if (url.startsWith("https://offline.test")) return new Response(null, { status: 200 });
    if (url.includes("engine-health")) return Response.json({ state: options.health ?? "healthy", dispatchReady: true });
    if (url.includes("compare/")) return Response.json({ status: options.compare ?? "identical", files: options.files ?? [] });
    if (url.includes("/runs?")) return Response.json({ workflow_runs: [{ status: "completed", conclusion: options.conclusion ?? "success", updated_at: "2026-11-03T03:00:00Z" }] });
    if (url.endsWith("dispatches")) return new Response(null, { status: options.dispatchStatus ?? 204 });
    return Response.json({ state: options.state ?? "active" });
  };
  return { calls, env, fetcher };
}
test("external clock dispatches at cadence, with independent success ping", async () => {
  const { calls, env, fetcher } = monitorFixture();
  assert.equal((await tick(env, new Date("2026-11-03T03:03:00Z"), fetcher)).healthy, true);
  assert.equal(calls.filter(([url]) => url.endsWith("dispatches")).length, 2);
  assert.equal(calls.at(-1)[0], "https://offline.test/ping");
  assert.ok(calls.every(([url]) => !url.endsWith("enable")));
});
for (const options of [
  { state: "disabled_inactivity" }, { state: "disabled_manually" }, { env: { RECOVERY_ARMED: "false" } },
  { compare: "diverged" }, { compare: "behind" }, { files: [{ filename: "alert_engine/backlog.py" }] },
  { dispatchStatus: 422 }, { dispatchStatus: 401 }, { health: "stopped" },
  { health: "unknown" }, { conclusion: "failure" },
]) {
  test(`external watchdog fails visibly: ${JSON.stringify(options)}`, async () => {
    const { calls, env, fetcher } = monitorFixture(options);
    assert.equal((await tick(env, new Date("2026-11-03T03:03:00Z"), fetcher)).healthy, false);
    assert.equal(calls.at(-1)[0], "https://offline.test/ping/fail");
    if (options.state || options.env || options.compare || options.files) assert.equal(calls.filter(([url]) => url.endsWith("dispatches")).length, 0);
  });
}
test("five-minute drain does not duplicate main 30-minute schedule", async () => {
  const { calls, env, fetcher } = monitorFixture();
  await tick(env, new Date("2026-11-03T03:08:00Z"), fetcher);
  assert.equal(calls.filter(([url]) => url.endsWith("dispatches")).length, 1);
});
