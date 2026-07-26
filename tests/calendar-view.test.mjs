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
  addCalendarMonths,
  calendarEventsForDate,
  calendarEventsForMonth,
  calendarMonthKey,
  groupCalendarEventsByDate,
  sortCalendarEventsByDate,
  startOfCalendarMonth,
} = loadTsModule("lib/calendar-view.ts");

function item(id, date, marks = {}) {
  return {
    id,
    eventId: id,
    canonicalEventId: `canonical:${id}`,
    date,
    ticker: id.toUpperCase(),
    type: "buy_by",
    star: Boolean(marks.star),
    heart: Boolean(marks.heart),
    bell: Boolean(marks.bell),
  };
}

test("current month initialization uses today instead of the oldest event", () => {
  const today = new Date(2026, 6, 26, 12);
  const events = [item("old", "2021-01-01")];

  assert.equal(calendarMonthKey(startOfCalendarMonth(today)), "2026-07");
  assert.equal(calendarEventsForMonth(events, startOfCalendarMonth(today)).length, 0);
});

test("previous and next navigation crosses year boundaries", () => {
  assert.equal(calendarMonthKey(addCalendarMonths(new Date(2026, 0, 1), -1)), "2025-12");
  assert.equal(calendarMonthKey(addCalendarMonths(new Date(2026, 11, 1), 1)), "2027-01");
});

test("month filtering returns only the selected month in ascending date order", () => {
  const july = new Date(2026, 6, 1);
  const events = [
    item("august", "2026-08-01"),
    item("late", "2026-07-31"),
    item("early", "2026-07-02"),
    item("june", "2026-06-30"),
  ];

  assert.deepEqual(
    calendarEventsForMonth(events, july).map((event) => event.id),
    ["early", "late"],
  );
});

test("same-day events retain their original stable ordering", () => {
  const events = [
    item("second-date", "2026-07-20"),
    item("first-same-day", "2026-07-10"),
    item("second-same-day", "2026-07-10"),
  ];

  assert.deepEqual(
    sortCalendarEventsByDate(events).map((event) => event.id),
    ["first-same-day", "second-same-day", "second-date"],
  );
});

test("day filtering supports populated and empty dates", () => {
  const events = [item("selected", "2026-07-23")];

  assert.deepEqual(
    calendarEventsForDate(events, "2026-07-23").map((event) => event.id),
    ["selected"],
  );
  assert.deepEqual(calendarEventsForDate(events, "2026-07-24"), []);
});

test("empty month produces no date groups", () => {
  const events = [item("outside", "2026-08-01")];
  const monthly = calendarEventsForMonth(events, new Date(2026, 6, 1));

  assert.deepEqual(groupCalendarEventsByDate(monthly), []);
});

test("filtering preserves event identity and star, heart, and bell state", () => {
  const marked = item("marked", "2026-07-23", { star: true, heart: true, bell: true });
  const [filtered] = calendarEventsForMonth([marked], new Date(2026, 6, 1));

  assert.equal(filtered, marked);
  assert.equal(filtered.eventId, "marked");
  assert.equal(filtered.canonicalEventId, "canonical:marked");
  assert.equal(filtered.star, true);
  assert.equal(filtered.heart, true);
  assert.equal(filtered.bell, true);
});

test("large datasets keep only the selected month in the rendered view model", () => {
  const events = Array.from({ length: 1_920 }, (_, index) => {
    const year = 2021 + Math.floor(index / 240);
    const month = (index % 12) + 1;
    const day = (index % 28) + 1;
    return item(
      `event-${index}`,
      `${year}-${String(month).padStart(2, "0")}-${String(day).padStart(2, "0")}`,
    );
  });
  const selectedMonth = new Date(2026, 6, 1);
  const monthly = calendarEventsForMonth(events, selectedMonth);

  assert.ok(monthly.length < events.length);
  assert.ok(monthly.every((event) => event.date.startsWith("2026-07-")));
});
