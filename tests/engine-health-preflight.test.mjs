import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);
function load(filename) {
  const output = ts.transpileModule(fs.readFileSync(filename, "utf8"), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText;
  const module = { exports: {} };
  Function("exports", "module", "require", output)(module.exports, module, (id) => id.startsWith("@/") ? load(id.replace("@/", "") + ".ts") : require(id));
  return module.exports;
}
async function withRoute(fetcher, check, overrides = {}) {
  const originalFetch = globalThis.fetch;
  const variables = { GITHUB_DISPATCH_TOKEN: "offline", NEXT_PUBLIC_FIREBASE_PROJECT_ID: "demo-goralert",
    GITHUB_REPO: "cococute88/goralert", GITHUB_WORKFLOW_FILE: "alert-test-push.yml", GITHUB_WORKFLOW_REF: "main", ...overrides };
  const saved = Object.fromEntries(Object.keys(variables).map((key) => [key, process.env[key]]));
  for (const [key, value] of Object.entries(variables)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
  globalThis.fetch = fetcher;
  try { await check(load("app/api/engine-health/route.ts")); }
  finally {
    globalThis.fetch = originalFetch;
    for (const [key, value] of Object.entries(saved)) { if (value === undefined) delete process.env[key]; else process.env[key] = value; }
  }
}

for (const row of [
  [200, "active", "ready", true, "healthy"],
  [404, "active", "heartbeat_missing", true, "unknown"],
  [403, "active", "firestore_forbidden", false, "unknown"],
  [401, "active", "firestore_forbidden", false, "unknown"],
  [503, "active", "firestore_unavailable", false, "unknown"],
  [200, "disabled_manually", "workflow_inactive", false, "stopped"],
  [200, "disabled_inactivity", "workflow_inactive", false, "stopped"],
  [200, "401", "github_unavailable", false, "unknown"],
]) {
  const [firestoreStatus, state, preflight, dispatchReady, expectedState] = row;
  test(`health GET: Firestore ${firestoreStatus}, workflow ${state}`, async () => {
    const fetcher = async (url, options) => {
      if (url.includes("firestore.googleapis.com")) {
        assert.equal(options.method, undefined); // GET-only, no auth/admin credential
        assert.equal(options.headers?.Authorization, undefined);
        return firestoreStatus === 200 ? Response.json({ fields: { lastSuccessfulRunAt: { stringValue: new Date().toISOString() } } }) : new Response(null, { status: firestoreStatus });
      }
      assert.ok(url.startsWith("https://api.github.com/repos/cococute88/goralert/"));
      return state === "401" ? new Response(null, { status: 401 }) : Response.json({ state });
    };
    await withRoute(fetcher, async (route) => {
      const response = await route.GET();
      const body = await response.json();
      assert.equal(body.preflight, preflight);
      assert.equal(body.dispatchReady, dispatchReady);
      assert.equal(body.state, expectedState);
      assert.equal(response.headers.get("cache-control"), "no-store");
    });
  });
}
test("wrong deployed target is not dispatchable even with a fresh heartbeat", async () => {
  await withRoute(async (url) => Response.json(url.includes("firestore") ? { fields: { lastSuccessfulRunAt: { stringValue: new Date().toISOString() } } } : { state: "active" }), async (route) => {
    const body = await (await route.GET()).json();
    assert.equal(body.preflight, "target_mismatch");
    assert.equal(body.dispatchReady, false);
    assert.equal(body.state, "unknown");
  }, { GITHUB_WORKFLOW_REF: "wrong-branch" });
});
test("missing server configuration makes no outbound requests", async () => {
  await withRoute(async () => { assert.fail("unexpected outbound call"); }, async (route) => {
    const body = await (await route.GET()).json();
    assert.equal(body.preflight, "configuration_unavailable");
    assert.equal(body.dispatchReady, false);
  }, { GITHUB_DISPATCH_TOKEN: undefined });
});

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;
test("real anonymous REST heartbeat read passes through the app route after rules", { skip: !emulatorHost }, async () => {
  assert.match(emulatorHost, /^(127\.0\.0\.1|localhost):\d+$/);
  const restPath = "/v1/projects/demo-goralert/databases/(default)/documents/systemHealth/alertEngine";
  const originalFetch = globalThis.fetch;
  // Fixture write only to the explicit loopback demo emulator. Production gets
  // no request, credential, data mutation or actual GitHub dispatch.
  const seeded = await originalFetch(`http://${emulatorHost}${restPath}`, { method: "PATCH", headers: { Authorization: "Bearer owner", "Content-Type": "application/json" },
    body: JSON.stringify({ fields: { lastSuccessfulRunAt: { stringValue: new Date().toISOString() } } }) });
  assert.equal(seeded.ok, true);
  await withRoute(async (url, options) => {
    if (url.startsWith("https://firestore.googleapis.com")) {
      assert.equal(new URL(url).pathname, restPath);
      return originalFetch(`http://${emulatorHost}${restPath}`, options);
    }
    assert.ok(url.startsWith("https://api.github.com/"));
    return Response.json({ state: "active" });
  }, async (route) => {
    const result = await (await route.GET()).json();
    assert.equal(result.state, "healthy");
    assert.equal(result.dispatchReady, true);
    assert.equal(result.preflight, "ready");
  });
});
