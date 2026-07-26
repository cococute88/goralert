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

const contract = loadTsModule("lib/calendar-contract.ts");
const display = loadTsModule("lib/calendar-display.ts");
const alerts = loadTsModule("lib/calendar-alerts.ts", {
  "@/lib/calendar-contract": contract,
  "@/lib/calendar-display": display,
});
const { ruleFormVisibility } = loadTsModule("components/alerts/forms/rule-form-mode.ts");

function event(id, overrides = {}) {
  return {
    id,
    eventId: id,
    canonicalEventId: id,
    identityKeys: [id],
    date: "2026-07-30",
    ticker: "CAG",
    type: "buy_by",
    title: "CAG 매수 마감",
    source: "calendarEvents",
    sourceKind: "declared",
    star: false,
    heart: false,
    ...overrides,
  };
}

function rule(id, selector, enabled = true) {
  return {
    id,
    uid: "user-1",
    kind: "date",
    name: id,
    enabled,
    condition: { kind: "date", selector },
    trigger: { mode: "recurring", recurrence: { kind: "calendar" } },
    delivery: { channels: ["push"] },
  };
}

test("bell is inactive without a matching active rule and active with one", () => {
  const selected = event("direct-cag");
  const matching = rule("matching", {
    source: "calendarEvents",
    match: { eventId: "direct-cag", date: "2026-07-30", type: "buy_by" },
  });

  assert.deepEqual([...alerts.deriveAlertedCalendarEventIds([selected], [])], []);
  assert.deepEqual([...alerts.deriveAlertedCalendarEventIds([selected], [matching])], ["direct-cag"]);
});

test("calendar cell indicator gives bell priority over heart and star", () => {
  const hearted = event("hearted", { heart: true });
  const both = event("both", { star: true, heart: true });

  assert.equal(alerts.calendarEventIndicator(hearted, new Set()), "heart");
  assert.equal(alerts.calendarEventIndicator(both, new Set(["both"])), "bell");
});

test("single-event draft hides mark filters and fixes identity, date, source, and type", () => {
  const selected = event("canonical-cag", {
    identityKeys: ["canonical-cag", "legacy-cag"],
  });
  const draft = alerts.buildSingleCalendarEventDraft(selected);
  const selector = draft.condition.selector;

  assert.equal(draft.trigger.mode, "once");
  assert.equal(selector.source, "calendarEvents");
  assert.equal(selector.markFilter, undefined);
  assert.deepEqual(selector.match, {
    eventId: "canonical-cag",
    date: "2026-07-30",
    ticker: "CAG",
    type: "buy_by",
  });
});

test("CAG direct rule does not spread to another matching ticker/type event", () => {
  const selected = event("selected-cag");
  const other = event("other-cag", { date: "2026-08-30" });
  const directRule = rule("direct", alerts.buildSingleCalendarEventDraft(selected).condition.selector);

  assert.equal(alerts.alertRuleTargetsCalendarEvent(directRule, selected), true);
  assert.equal(alerts.alertRuleTargetsCalendarEvent(directRule, other), false);
});

test("custom NFP direct rule matches only its compatible identity", () => {
  const nfp = event("custom:nfp:2026-07-30", {
    identityKeys: ["custom:nfp:2026-07-30", "legacy-nfp"],
    ticker: "",
    type: "custom",
    title: "NFP(21:30)",
    source: "calendarCustomEvents",
    sourceKind: "custom",
  });
  const other = event("custom:nfp:2026-08-07", {
    ticker: "",
    type: "custom",
    title: "NFP(21:30)",
    source: "calendarCustomEvents",
    sourceKind: "custom",
    date: "2026-08-07",
  });
  const directRule = rule("nfp", alerts.buildSingleCalendarEventDraft(nfp).condition.selector);

  assert.equal(alerts.alertRuleTargetsCalendarEvent(directRule, nfp), true);
  assert.equal(alerts.alertRuleTargetsCalendarEvent(directRule, other), false);
});

test("disabled or removed direct rules immediately derive an inactive bell", () => {
  const selected = event("selected");
  const selector = alerts.buildSingleCalendarEventDraft(selected).condition.selector;
  const disabled = rule("disabled", selector, false);

  assert.equal(alerts.deriveAlertedCalendarEventIds([selected], [disabled]).size, 0);
  assert.equal(alerts.deriveAlertedCalendarEventIds([selected], []).size, 0);
});

test("existing broad filter rules retain their matching behavior", () => {
  const selected = event("selected", { star: true });
  const general = rule("general", {
    source: "calendarEvents",
    match: { ticker: "CAG", type: ["buy_by"] },
    markFilter: ["star"],
  });

  assert.equal(alerts.alertRuleTargetsCalendarEvent(general, selected), true);
});

test("single-event form removes broad filters while the standard form keeps them", () => {
  assert.deepEqual(ruleFormVisibility("single-calendar-event"), {
    perKindFilters: false,
    singleEventSummary: true,
    messageTemplate: false,
    advancedSettings: false,
    favoriteAction: false,
  });
  assert.deepEqual(ruleFormVisibility("standard"), {
    perKindFilters: true,
    singleEventSummary: false,
    messageTemplate: true,
    advancedSettings: true,
    favoriteAction: true,
  });
});

test("AND composite bells require every calendar branch to share the evaluation date", () => {
  const first = event("first", { ticker: "A", date: "2026-07-29" });
  const differentDate = event("second", { ticker: "B", date: "2026-07-30" });
  const sameDate = event("third", { ticker: "B", date: "2026-07-29" });
  const composite = {
    ...rule("and-rule", { source: "calendarEvents" }),
    kind: "composite",
    condition: {
      kind: "composite",
      operator: "and",
      conditions: [
        { kind: "date", selector: { source: "calendarEvents", match: { eventId: "first" } } },
        {
          kind: "date",
          selector: {
            source: "calendarEvents",
            match: { ticker: "B", type: "buy_by" },
          },
        },
      ],
    },
  };

  assert.deepEqual(
    [...alerts.deriveAlertedCalendarEventIds([first, differentDate], [composite])],
    [],
  );
  assert.deepEqual(
    [...alerts.deriveAlertedCalendarEventIds([first, sameDate], [composite])],
    ["first", "third"],
  );
});

test("single-event occurrence must still be in the future in Asia/Seoul", () => {
  const selected = event("future", { date: "2026-07-29" });
  const draft = alerts.buildSingleCalendarEventDraft(selected);

  assert.equal(
    alerts.isSingleCalendarEventOccurrenceFuture(draft, new Date("2026-07-26T00:00:00.000Z")),
    true,
  );
  assert.equal(
    alerts.isSingleCalendarEventOccurrenceFuture(draft, new Date("2026-07-29T00:00:01.000Z")),
    false,
  );
  assert.equal(
    alerts.isSingleCalendarEventOccurrenceFuture(
      alerts.buildSingleCalendarEventDraft(event("past", { date: "2026-07-25" })),
      new Date("2026-07-26T00:00:00.000Z"),
    ),
    false,
  );
});
