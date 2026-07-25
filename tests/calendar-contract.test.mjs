import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

function loadCalendarContract() {
  const filename = "lib/calendar-contract.ts";
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: {
      module: ts.ModuleKind.CommonJS,
      target: ts.ScriptTarget.ES2022,
    },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  Function("exports", "module", output)(module.exports, module);
  return module.exports;
}

const { resolveGeneratedCalendarEvents } = loadCalendarContract();

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
