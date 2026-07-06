"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (US-008)
// 지표 알림 폼 (RSI/VIX/가격/환율/금/비트코인/국내ETF). 지표 종류에 따라 필요한
// 입력만 노출하고, rule.kind 와 condition.kind 를 함께 동기화한다. 확인(발송) 시각은
// calendar recurrence 의 time 으로 저장한다(예: KOSPI 종가 확인 15:35).

import type { Comparator, MetricCondition, MetricId } from "@/lib/alerts/types";
import { Field, NumberInput, Select, TimeInput, TextInput, COMPARATOR_OPTIONS, SEOUL_TZ, type RuleFormProps } from "./fields";

type MetricKind = MetricCondition["kind"];

const METRIC_OPTIONS: { value: MetricKind; label: string }[] = [
  { value: "rsi", label: "RSI (상대강도지수)" },
  { value: "price", label: "가격" },
  { value: "vix", label: "VIX (변동성)" },
  { value: "fx", label: "환율" },
  { value: "koreanEtf", label: "국내 ETF" },
  { value: "gold", label: "금" },
  { value: "bitcoin", label: "비트코인" },
];

function defaultMetricId(kind: MetricKind): MetricId {
  switch (kind) {
    case "rsi":
      return { metric: "rsi", ticker: "KOSPI", period: 14 };
    case "price":
      return { metric: "price", ticker: "" };
    case "fx":
      return { metric: "fx", pair: "USDKRW" };
    case "koreanEtf":
      return { metric: "koreanEtf", code: "" };
    case "vix":
      return { metric: "vix" };
    case "gold":
      return { metric: "gold" };
    case "bitcoin":
      return { metric: "bitcoin" };
    default:
      return { metric: "vix" };
  }
}

function asMetric(condition: MetricCondition | undefined): MetricCondition {
  if (condition && "metric" in condition) return condition;
  return { kind: "rsi", metric: defaultMetricId("rsi"), comparator: "lte", threshold: 50 };
}

export default function MetricForm({ value, onChange }: RuleFormProps) {
  const condition = asMetric(value.condition?.kind && ["rsi", "vix", "price", "fx", "gold", "bitcoin", "koreanEtf"].includes(value.condition.kind) ? (value.condition as MetricCondition) : undefined);
  const metricId = condition.metric;
  const recurrence = value.trigger?.recurrence;

  const updateCondition = (next: MetricCondition) => {
    // rule.kind must mirror condition.kind for non-composite rules.
    onChange({ ...value, kind: next.kind, condition: next });
  };

  const updateMetricId = (patch: Record<string, string | number>) => {
    updateCondition({ ...condition, metric: { ...metricId, ...patch } as MetricId });
  };

  const changeKind = (kind: MetricKind) => {
    updateCondition({ ...condition, kind, metric: defaultMetricId(kind) });
  };

  const updateFireTime = (time: string) => {
    onChange({
      ...value,
      kind: condition.kind,
      condition,
      trigger: {
        mode: "recurring",
        ...value.trigger,
        recurrence: { kind: "calendar", tz: SEOUL_TZ, ...recurrence, time },
      },
    });
  };

  return (
    <div className="space-y-3">
      <Field label="지표 종류">
        <Select value={condition.kind} onChange={(e) => changeKind(e.target.value as MetricKind)}>
          {METRIC_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      </Field>

      {metricId.metric === "rsi" ? (
        <div className="grid grid-cols-2 gap-2">
          <Field label="종목/지수">
            <TextInput value={metricId.ticker} placeholder="예: KOSPI" onChange={(e) => updateMetricId({ ticker: e.target.value.toUpperCase() })} />
          </Field>
          <Field label="기간(period)">
            <NumberInput value={metricId.period} min={1} onChange={(e) => updateMetricId({ period: Number(e.target.value) })} />
          </Field>
        </div>
      ) : null}

      {metricId.metric === "price" ? (
        <Field label="종목">
          <TextInput value={metricId.ticker} placeholder="예: AAPL" onChange={(e) => updateMetricId({ ticker: e.target.value.toUpperCase() })} />
        </Field>
      ) : null}

      {metricId.metric === "fx" ? (
        <Field label="통화쌍">
          <TextInput value={metricId.pair} placeholder="예: USDKRW" onChange={(e) => updateMetricId({ pair: e.target.value.toUpperCase() })} />
        </Field>
      ) : null}

      {metricId.metric === "koreanEtf" ? (
        <Field label="ETF 코드">
          <TextInput value={metricId.code} placeholder="예: 379800" onChange={(e) => updateMetricId({ code: e.target.value })} />
        </Field>
      ) : null}

      <div className="grid grid-cols-2 gap-2">
        <Field label="조건">
          <Select value={condition.comparator} onChange={(e) => updateCondition({ ...condition, comparator: e.target.value as Comparator })}>
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
            placeholder="예: 50"
            onChange={(e) => updateCondition({ ...condition, threshold: e.target.value === "" ? Number.NaN : Number(e.target.value) })}
          />
        </Field>
      </div>

      <Field label="확인 시각" hint="이 시각(예: 종가 확인 15:35)에 지표를 확인합니다.">
        <TimeInput value={recurrence?.time ?? "15:35"} onChange={(e) => updateFireTime(e.target.value)} />
      </Field>
    </div>
  );
}
