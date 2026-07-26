import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

function loadTsModule(filename, dependencies = {}) {
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  Function("exports", "module", "require", output)(
    module.exports,
    module,
    (specifier) => {
      if (specifier in dependencies) return dependencies[specifier];
      throw new Error(`Unexpected runtime import from ${filename}: ${specifier}`);
    },
  );
  return module.exports;
}

const calendarContract = loadTsModule("lib/calendar-contract.ts");
const {
  calendarIdentityKeys,
  findMatchingCalendarIdentityKey,
  normalizeAuthoritativeCalendarEvent,
  resolveGeneratedCalendarEvents,
} = calendarContract;
const { nextRuleOccurrence } = loadTsModule("lib/alerts/schedule.ts", {
  "@/lib/calendar-contract": calendarContract,
});

function event(ticker, id, date = "2026-08-10") {
  return {
    id,
    canonicalEventId: id,
    ticker,
    type: "buy_by",
    date,
    title: `${ticker} deadline`,
    source: "calendarEvents",
    star: false,
    heart: false,
  };
}

test("empty authoritative cache suppresses stale legacy events", () => {
  const legacy = event("TEST", "legacy-test");

  assert.deepEqual(resolveGeneratedCalendarEvents([], [legacy], [], ["TEST"]), []);
});

test("populated authoritative cache supersedes legacy events", () => {
  const cache = event("TEST", "cache-test");
  const legacy = event("TEST", "legacy-test", "2026-07-01");

  assert.deepEqual(resolveGeneratedCalendarEvents([cache], [legacy], [], ["TEST"]), [cache]);
});

test("missing cache document preserves the legacy fallback", () => {
  const legacy = event("TEST", "legacy-test");

  assert.deepEqual(resolveGeneratedCalendarEvents([], [legacy], [], []), [legacy]);
});

test("empty and populated cache tickers resolve independently", () => {
  const legacyTest = event("TEST", "legacy-test");
  const legacyAbc = event("ABC", "legacy-abc", "2026-07-01");
  const cacheAbc = event("ABC", "cache-abc");

  assert.deepEqual(
    resolveGeneratedCalendarEvents(
      [cacheAbc],
      [legacyTest, legacyAbc],
      [],
      ["TEST", "ABC"],
    ),
    [cacheAbc],
  );
});

function metricRule(time = "15:35") {
  return {
    id: "metric-rule",
    uid: "user-1",
    kind: "rsi",
    name: "KOSPI RSI",
    enabled: true,
    condition: {
      kind: "rsi",
      metric: { metric: "rsi", ticker: "KOSPI", period: 14 },
      comparator: "lte",
      threshold: 50,
    },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "calendar", time, tz: "Asia/Seoul" },
    },
    delivery: { channels: ["push"] },
  };
}

test("calendar-kind metric rule keeps its next daily fixed evaluation time", () => {
  const before = new Date("2026-08-10T05:00:00.000Z"); // 14:00 KST
  const after = new Date("2026-08-10T07:00:00.000Z"); // 16:00 KST

  assert.equal(
    nextRuleOccurrence(metricRule(), [], before)?.toISOString(),
    "2026-08-10T06:35:00.000Z",
  );
  assert.equal(
    nextRuleOccurrence(metricRule(), [], after)?.toISOString(),
    "2026-08-11T06:35:00.000Z",
  );
});

test("date selector still resolves its next occurrence from calendar events", () => {
  const rule = {
    ...metricRule("09:00"),
    id: "calendar-rule",
    kind: "date",
    condition: {
      kind: "date",
      selector: { source: "calendarEvents", match: { type: ["buy_by"] } },
    },
  };
  const calendarEvent = event("TEST", "cache-test");

  assert.equal(
    nextRuleOccurrence(rule, [calendarEvent], new Date("2026-08-09T00:00:00.000Z"))?.toISOString(),
    "2026-08-10T00:00:00.000Z",
  );
});

test("dividend selector resolves its next occurrence from calendar events", () => {
  const rule = {
    ...metricRule("08:00"),
    id: "dividend-calendar-rule",
    kind: "dividend",
    condition: {
      kind: "dividend",
      ticker: "TEST",
      selector: { source: "calendarEvents", match: { type: ["buy_by"] } },
    },
  };
  const calendarEvent = event("TEST", "cache-test");

  assert.equal(
    nextRuleOccurrence(rule, [calendarEvent], new Date("2026-08-09T00:00:00.000Z"))?.toISOString(),
    "2026-08-09T23:00:00.000Z",
  );
});

test("nested composite selector resolves its next calendar occurrence", () => {
  const selectorCondition = {
    kind: "date",
    selector: { source: "calendarEvents", match: { type: ["buy_by"] } },
  };
  const rule = {
    ...metricRule("23:45"),
    id: "nested-calendar-composite",
    kind: "composite",
    condition: {
      kind: "composite",
      operator: "and",
      conditions: [{
        kind: "composite",
        operator: "or",
        conditions: [selectorCondition],
      }],
    },
  };

  assert.equal(
    nextRuleOccurrence(rule, [event("TEST", "cache-test")], new Date("2026-08-09T00:00:00.000Z"))?.toISOString(),
    "2026-08-10T14:45:00.000Z",
  );
});

test("mixed selector OR metric composite remains a daily schedule", () => {
  const rule = {
    ...metricRule("15:35"),
    id: "mixed-or-composite",
    kind: "composite",
    condition: {
      kind: "composite",
      operator: "or",
      conditions: [
        {
          kind: "date",
          selector: { source: "calendarEvents", match: { type: ["buy_by"] } },
        },
        metricRule().condition,
      ],
    },
  };

  assert.equal(
    nextRuleOccurrence(rule, [event("TEST", "cache-test")], new Date("2026-08-09T00:00:00.000Z"))?.toISOString(),
    "2026-08-09T06:35:00.000Z",
  );
});

test("dividend minus-one selector preview uses the evaluator's raw event date", () => {
  const rule = {
    ...metricRule("08:00"),
    id: "dividend-minus-one-rule",
    kind: "dividend",
    condition: {
      kind: "dividend",
      ticker: "TEST",
      selector: { source: "calendarEvents", match: { type: ["buy_by_minus_1"] } },
    },
  };

  assert.equal(
    nextRuleOccurrence(rule, [event("TEST", "cache-test")], new Date("2026-08-08T00:00:00.000Z"))?.toISOString(),
    "2026-08-09T23:00:00.000Z",
  );
});

test("legacy Firestore document identity still matches existing bell marks", () => {
  const legacy = normalizeAuthoritativeCalendarEvent(
    {
      ...event("TEST", "payload-id"),
      id: undefined,
      canonicalEventId: "dividend:TEST:buy:2026-08-10",
      firestoreDocumentId: "legacy-firestore-doc",
    },
    "legacy-firestore-doc",
  );
  const keys = calendarIdentityKeys(legacy);

  assert.equal(
    findMatchingCalendarIdentityKey(keys, ["legacy-firestore-doc"]),
    "legacy-firestore-doc",
  );
});
