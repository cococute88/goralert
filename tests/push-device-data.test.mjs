import assert from "node:assert/strict";
import test from "node:test";
import {
  MAX_PUSH_REGISTRATIONS,
  effectivePushTokens,
  normalizePushDevices,
  removePushRegistrationsData,
  resetPushRegistrationData,
  upsertPushDeviceData,
} from "../lib/alerts/push-device-data.mjs";

const NOW = "2026-07-18T00:00:00.000Z";

function device(id, token, overrides = {}) {
  return {
    id,
    token,
    label: "Android · Chrome",
    platform: "android",
    browser: "chrome",
    deviceType: "mobile",
    registeredAt: "2026-07-10T00:00:00.000Z",
    lastSeenAt: "2026-07-10T00:00:00.000Z",
    tokenUpdatedAt: "2026-07-10T00:00:00.000Z",
    ...overrides,
  };
}

test("legacy, metadata, and mixed registrations produce one delivery per unique token", () => {
  const data = {
    pushTokens: ["legacy-a", "shared", "legacy-a"],
    pushDevices: [device("device-1", "shared"), device("device-2", "device-b")],
  };
  assert.deepEqual(effectivePushTokens(data), ["shared", "device-b", "legacy-a"]);
});

test("same local device id replaces a refreshed token and preserves first registration", () => {
  const next = upsertPushDeviceData(
    { pushTokens: ["old-token", "other-token"], pushDevices: [device("device-1", "old-token")] },
    device("device-1", "new-token", { registeredAt: NOW, lastSeenAt: NOW, tokenUpdatedAt: NOW }),
    NOW,
  );
  assert.deepEqual(next.pushTokens, ["other-token", "new-token"]);
  assert.equal(next.pushDevices.length, 1);
  assert.equal(next.pushDevices[0].token, "new-token");
  assert.equal(next.pushDevices[0].registeredAt, "2026-07-10T00:00:00.000Z");
});

test("confirmation never recreates a device entry removed by a full reset", () => {
  const next = upsertPushDeviceData(
    { pushTokens: [], pushDevices: [] },
    device("stale-local-id", "local-token"),
    NOW,
    { requireExistingDeviceId: true },
  );
  assert.equal(next.changed, false);
  assert.deepEqual(next.pushTokens, []);
  assert.deepEqual(next.pushDevices, []);
});

test("exact device deletion is idempotent and never removes a sibling", () => {
  const current = {
    pushTokens: ["token-a", "token-b", "legacy-c"],
    pushDevices: [device("device-a", "token-a"), device("device-b", "token-b")],
  };
  const first = removePushRegistrationsData(current, [{ deviceId: "device-a" }]);
  assert.deepEqual(first.pushTokens, ["token-b", "legacy-c"]);
  assert.deepEqual(first.pushDevices.map((item) => item.id), ["device-b"]);
  assert.equal(first.removedCount, 1);
  const repeated = removePushRegistrationsData(first, [{ deviceId: "device-a" }]);
  assert.deepEqual(repeated.pushTokens, first.pushTokens);
  assert.deepEqual(repeated.pushDevices, first.pushDevices);
  assert.equal(repeated.removedCount, 0);
});

test("legacy token can be removed exactly and malformed metadata is discarded", () => {
  assert.deepEqual(normalizePushDevices([{ id: "missing-token" }, null, device("ok", "token-ok")]), [
    device("ok", "token-ok"),
  ]);
  const next = removePushRegistrationsData(
    { pushTokens: ["legacy-a", "legacy-b"], pushDevices: [] },
    [{ token: "legacy-a" }],
  );
  assert.deepEqual(next.pushTokens, ["legacy-b"]);
});

test("full reset clears both legacy tokens and device metadata", () => {
  assert.deepEqual(resetPushRegistrationData(), { pushTokens: [], pushDevices: [] });
});

test("registration cap rejects growth without evicting an existing target", () => {
  const full = Array.from({ length: MAX_PUSH_REGISTRATIONS }, (_, index) => `legacy-${index}`);
  assert.throws(
    () => upsertPushDeviceData({ pushTokens: full, pushDevices: [] }, device("new-device", "new-token"), NOW),
    /100대/,
  );
});
