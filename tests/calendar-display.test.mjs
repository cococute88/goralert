import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

function loadTsModule(filename) {
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
      throw new Error(`Unexpected runtime import from ${filename}: ${specifier}`);
    },
  );
  return module.exports;
}

const {
  MANUAL_CALENDAR_TICKERS_SOURCE,
  MANUAL_CALENDAR_TICKERS_VERSION,
  buildCalendarDisplayAlertMatch,
  buildCalendarDayCellModel,
  filterCalendarDisplayEvents,
  resolveCalendarDisplayTickerUniverse,
} = loadTsModule("lib/calendar-display.ts");

function regular(id, ticker, type = "buy_by", marks = {}) {
  return {
    id,
    eventId: id,
    canonicalEventId: id,
    date: "2026-07-30",
    ticker,
    type,
    title: `${ticker} event`,
    source: "calendarEvents",
    sourceKind: "declared",
    star: Boolean(marks.star),
    heart: Boolean(marks.heart),
  };
}

function custom(id, title, source = "calendarCustomEvents") {
  return {
    id,
    eventId: id,
    canonicalEventId: id,
    date: "2026-07-30",
    ticker: "",
    type: "custom",
    title,
    source,
    sourceKind: "custom",
    star: false,
    heart: false,
  };
}

test("default display universe follows Gorani source priority and ignores stale manual shapes", () => {
  const staleManual = { tickers: ["STALE"] };
  const fromLegacy = resolveCalendarDisplayTickerUniverse({
    portfolioId: "default",
    manualOverride: staleManual,
    legacyPortfolioTickers: ["CAG", "D", "F"],
    legacyEventTickers: ["EVENT-FALLBACK"],
    legacyMemoKeys: ["MEMO-FALLBACK"],
  });
  assert.deepEqual(fromLegacy, {
    source: "legacy-portfolios",
    tickers: ["CAG", "D", "F"],
  });

  const validManual = resolveCalendarDisplayTickerUniverse({
    portfolioId: "default",
    manualOverride: {
      source: MANUAL_CALENDAR_TICKERS_SOURCE,
      version: MANUAL_CALENDAR_TICKERS_VERSION,
      tickers: ["APAM"],
    },
    legacyPortfolioTickers: ["CAG"],
  });
  assert.deepEqual(validManual, { source: "manual", tickers: ["APAM"] });
});

test("named portfolios use only their namespaced ticker settings", () => {
  const named = resolveCalendarDisplayTickerUniverse({
    portfolioId: "income",
    manualOverride: { tickers: ["BXSL", "FEPI"] },
    legacyPortfolioTickers: ["STALE"],
    legacyEventTickers: ["STALE-EVENT"],
  });
  assert.deepEqual(named, { source: "manual", tickers: ["BXSL", "FEPI"] });

  const cacheBacked = resolveCalendarDisplayTickerUniverse({
    portfolioId: "income-without-settings",
    portfolioEventTickers: ["BXSL", "FEPI"],
    legacyPortfolioTickers: ["SHOULD-NOT-LEAK"],
  });
  assert.deepEqual(cacheBacked, {
    source: "portfolio-events",
    tickers: ["BXSL", "FEPI"],
  });
  assert.deepEqual(
    filterCalendarDisplayEvents(
      [regular("bxsl", "BXSL"), regular("fepi", "FEPI")],
      cacheBacked.tickers,
    ).map((event) => event.ticker),
    ["BXSL", "FEPI"],
  );

  const explicitlyEmpty = resolveCalendarDisplayTickerUniverse({
    portfolioId: "empty",
    manualOverride: { tickers: [] },
    portfolioEventTickers: ["SHOULD-NOT-LEAK"],
  });
  assert.deepEqual(explicitlyEmpty, { source: "manual", tickers: [] });
});

test("a valid empty default manual override remains authoritative", () => {
  const result = resolveCalendarDisplayTickerUniverse({
    portfolioId: "default",
    manualOverride: {
      source: MANUAL_CALENDAR_TICKERS_SOURCE,
      version: MANUAL_CALENDAR_TICKERS_VERSION,
      tickers: [],
    },
    legacyPortfolioTickers: ["STALE"],
  });

  assert.deepEqual(result, { source: "manual", tickers: [] });
});

test("display filtering excludes stale tickers without ticker-specific rules", () => {
  const active = regular("active", "CAG");
  const stale = regular("stale", "STALE");
  const economic = custom("custom:fomc", "FOMC");
  const result = filterCalendarDisplayEvents([stale, economic, active], ["CAG"]);

  assert.deepEqual(result.map((event) => event.id), ["custom:fomc", "active"]);
  assert.equal(result.includes(stale), false);
});

test("duplicate custom identity prefers the custom collection and renders once", () => {
  const imported = custom("custom:cpi", "CPI(21:30)", "calendarEvents");
  const authoritative = custom("custom:cpi", "CPI(21:30)", "calendarCustomEvents");
  const result = filterCalendarDisplayEvents([imported, authoritative], []);

  assert.deepEqual(result, [authoritative]);
});

test("case 1: custom FOMC stays beside the date while three regular events keep real tickers", () => {
  const model = buildCalendarDayCellModel([
    regular("cag", "CAG"),
    custom("custom:fomc", "FOMC"),
    regular("d", "D"),
    regular("f", "F"),
  ]);

  assert.deepEqual(model.customEvents.map((event) => event.title), ["FOMC"]);
  assert.deepEqual(model.visibleRegularEvents.map((event) => event.ticker), ["CAG", "D", "F"]);
  assert.equal(model.regularOverflowCount, 0);
});

test("case 2: custom CPI does not consume the one regular event slot", () => {
  const model = buildCalendarDayCellModel([
    custom("custom:cpi", "CPI(21:30)"),
    regular("cag", "CAG"),
  ]);

  assert.equal(model.customEvents[0].title, "CPI(21:30)");
  assert.deepEqual(model.visibleRegularEvents.map((event) => event.ticker), ["CAG"]);
});

test("case 3: three regular events are shown individually without ticker aggregation", () => {
  const model = buildCalendarDayCellModel([
    regular("nnn", "NNN"),
    regular("apam", "APAM"),
    regular("cag", "CAG"),
  ]);

  assert.deepEqual(model.visibleRegularEvents.map((event) => event.ticker), ["APAM", "CAG", "NNN"]);
  assert.equal(model.regularOverflowCount, 0);
});

test("case 4: five regular events show three items plus an independent overflow count", () => {
  const events = ["A", "B", "C", "D", "E"].map((ticker) => regular(ticker, ticker));
  const model = buildCalendarDayCellModel(events);

  assert.deepEqual(model.visibleRegularEvents.map((event) => event.ticker), ["A", "B", "C"]);
  assert.equal(model.regularOverflowCount, 2);
  assert.deepEqual(model.regularEvents.map((event) => event.ticker), ["A", "B", "C", "D", "E"]);
});

test("case 6: distinct identities for the same ticker are never deduped by ticker text", () => {
  const buy = regular("same-buy", "SAME", "buy_by");
  const exDiv = regular("same-ex", "SAME", "ex_div");
  const filtered = filterCalendarDisplayEvents([buy, exDiv], ["SAME"]);

  assert.equal(filtered.length, 2);
  assert.deepEqual(
    buildCalendarDayCellModel(filtered).visibleRegularEvents.map((event) => event.id),
    ["same-ex", "same-buy"],
  );
});

test("case 7: one custom and three regular events are all represented at once", () => {
  const model = buildCalendarDayCellModel([
    custom("custom:nfp", "NFP(21:30)"),
    regular("earn", "MSFT", "earnings"),
    regular("buy", "CAG", "buy_by"),
    regular("ex", "NNN", "ex_div"),
  ]);

  assert.equal(model.customEvents.length, 1);
  assert.equal(model.visibleRegularEvents.length, 3);
  assert.equal(model.regularOverflowCount, 0);
});

test("case 8: filtering and cell projection preserve star, heart, and event identity", () => {
  const starred = regular("starred", "STAR", "pay", { star: true });
  const hearted = regular("hearted", "HEART", "buy_by", { heart: true });
  const filtered = filterCalendarDisplayEvents([hearted, starred], ["STAR", "HEART"]);
  const model = buildCalendarDayCellModel(filtered);

  assert.equal(filtered.find((event) => event.id === "starred"), starred);
  assert.equal(filtered.find((event) => event.id === "hearted"), hearted);
  assert.deepEqual(model.visibleRegularEvents.map((event) => event.id), ["starred", "hearted"]);
});

test("custom alert match scopes the draft to the selected title", () => {
  assert.deepEqual(
    buildCalendarDisplayAlertMatch(custom("custom:fomc", "FOMC")),
    { type: "custom", titleContains: "FOMC" },
  );
  assert.deepEqual(
    buildCalendarDisplayAlertMatch(regular("cag", "CAG", "ex_div")),
    { ticker: "CAG", type: "ex_div" },
  );
});
