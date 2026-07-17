import assert from "node:assert/strict";
import test from "node:test";
import { serverTimestamp } from "firebase/firestore";
import { sanitizeFirestorePayload } from "../lib/alerts/firestore-payload.mjs";

test("removes undefined recursively while preserving valid primitive values", () => {
  const payload = sanitizeFirestorePayload({
    falseValue: false,
    zero: 0,
    nullable: null,
    emptyString: "",
    emptyArray: [],
    nested: { remove: undefined, keep: "value", deeper: { remove: undefined, keep: false } },
    values: [undefined, 0, false, null, "", { remove: undefined, keep: "ok" }],
  });

  assert.deepEqual(payload, {
    falseValue: false,
    zero: 0,
    nullable: null,
    emptyString: "",
    emptyArray: [],
    nested: { keep: "value", deeper: { keep: false } },
    values: [0, false, null, "", { keep: "ok" }],
  });
});

test("preserves Firestore FieldValue and prototype-bearing special values by identity", () => {
  const timestamp = serverTimestamp();
  const date = new Date("2026-07-17T00:00:00.000Z");
  class SpecialValue {}
  const special = new SpecialValue();

  const payload = sanitizeFirestorePayload({ timestamp, date, special, nested: { timestamp } });

  assert.equal(payload.timestamp, timestamp);
  assert.equal(payload.nested.timestamp, timestamp);
  assert.equal(payload.date, date);
  assert.equal(payload.special, special);
});
