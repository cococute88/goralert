import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import ts from "typescript";

function loadTsModule(filename) {
  const source = fs.readFileSync(filename, "utf8");
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
    fileName: filename,
  }).outputText;
  const module = { exports: {} };
  Function("exports", "module", "require", output)(module.exports, module, () => {
    throw new Error(`Unexpected runtime import from ${filename}`);
  });
  return module.exports;
}

const { validateAlertRule } = loadTsModule("lib/alerts/validation.ts");

function rule(kind, condition) {
  return {
    id: "rule",
    uid: "user",
    kind,
    name: "계약 테스트",
    enabled: true,
    condition,
    trigger: { mode: "recurring" },
    delivery: { channels: ["push"] },
  };
}

test("metric rules reject missing identifiers before Firestore save", () => {
  const cases = [
    rule("price", { kind: "price", metric: { metric: "price", ticker: " " }, comparator: "gte", threshold: 1 }),
    rule("rsi", { kind: "rsi", metric: { metric: "rsi", ticker: "KOSPI", period: 0 }, comparator: "lte", threshold: 50 }),
    rule("fx", { kind: "fx", metric: { metric: "fx", pair: "" }, comparator: "gte", threshold: 1 }),
    rule("koreanEtf", { kind: "koreanEtf", metric: { metric: "koreanEtf", code: "" }, comparator: "gte", threshold: 1 }),
  ];

  for (const candidate of cases) assert.equal(validateAlertRule(candidate).ok, false);
});

test("ratio direction requires both numerator and denominator", () => {
  const invalid = rule("ratio", {
    kind: "ratio",
    numerator: "MSFT",
    denominator: " ",
    comparator: "gte",
    threshold: 3,
  });
  const valid = rule("ratio", {
    kind: "ratio",
    numerator: "MSFT",
    denominator: "SCHD",
    comparator: "gte",
    threshold: 3,
  });

  assert.equal(validateAlertRule(invalid).ok, false);
  assert.equal(validateAlertRule(valid).ok, true);
});

test("composite rules validate operator and child market inputs", () => {
  const invalid = rule("composite", {
    kind: "composite",
    operator: "or",
    conditions: [{
      kind: "price",
      metric: { metric: "price", ticker: "" },
      comparator: "gte",
      threshold: 100,
    }],
  });
  assert.equal(validateAlertRule(invalid).ok, false);
});
