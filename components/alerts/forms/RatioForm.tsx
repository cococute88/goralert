"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (US-004/005)
// 전환비 알림 폼: numerator/denominator 종목, 비교 연산자, 기준값.

import type { Comparator, RatioCondition } from "@/lib/alerts/types";
import { Field, NumberInput, Select, TextInput, COMPARATOR_OPTIONS, type RuleFormProps } from "./fields";

function asRatio(value: Partial<RatioCondition> | undefined): RatioCondition {
  return {
    kind: "ratio",
    numerator: value?.numerator ?? "",
    denominator: value?.denominator ?? "",
    comparator: value?.comparator ?? "gte",
    threshold: value?.threshold ?? 0,
  };
}

export default function RatioForm({ value, onChange }: RuleFormProps) {
  const condition = asRatio(value.condition?.kind === "ratio" ? value.condition : undefined);

  const update = (patch: Partial<RatioCondition>) => {
    onChange({ ...value, condition: { ...condition, ...patch } });
  };

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-2">
        <Field label="분자 종목">
          <TextInput
            value={condition.numerator}
            placeholder="예: SPY"
            onChange={(e) => update({ numerator: e.target.value.toUpperCase() })}
          />
        </Field>
        <Field label="분모 종목">
          <TextInput
            value={condition.denominator}
            placeholder="예: SCHD"
            onChange={(e) => update({ denominator: e.target.value.toUpperCase() })}
          />
        </Field>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <Field label="조건">
          <Select value={condition.comparator} onChange={(e) => update({ comparator: e.target.value as Comparator })}>
            {COMPARATOR_OPTIONS.map((opt) => (
              <option key={opt.value} value={opt.value}>
                {opt.label}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="기준값">
          <NumberInput
            value={Number.isFinite(condition.threshold) ? condition.threshold : ""}
            placeholder="예: 25"
            onChange={(e) => update({ threshold: e.target.value === "" ? Number.NaN : Number(e.target.value) })}
          />
        </Field>
      </div>

      <p className="text-[11px] text-muted-foreground">
        분자/분모 가격의 비율이 기준값 조건을 만족하면 알림이 발송됩니다.
      </p>
    </div>
  );
}
