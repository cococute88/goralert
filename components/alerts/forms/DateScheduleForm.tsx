"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (US-001/002/003)
// 날짜 기반 반복 알림 폼. recurrence.kind 에 따라 필요한 필드만 노출한다
// (Progressive Disclosure): 격주/주간은 요일 선택, 말일/1일은 요일 숨김.

import type { DateCondition, Recurrence } from "@/lib/alerts/types";
import { Field, Select, TimeInput, TextInput, SEOUL_TZ, type RuleFormProps } from "./fields";

const RECURRENCE_OPTIONS: { value: Recurrence["kind"]; label: string }[] = [
  { value: "biweekly", label: "격주 (2주마다)" },
  { value: "weekly", label: "매주" },
  { value: "monthlyLastDay", label: "매월 말일" },
  { value: "monthlyFirstDay", label: "매월 1일" },
];

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

export default function DateScheduleForm({ value, onChange }: RuleFormProps) {
  const condition = (value.condition?.kind === "date" ? value.condition : { kind: "date" }) as DateCondition;
  const recurrence: Recurrence = value.trigger?.recurrence ?? { kind: "biweekly", weekday: 6, time: "08:00", tz: SEOUL_TZ };

  const updateRecurrence = (patch: Partial<Recurrence>) => {
    onChange({
      ...value,
      condition,
      trigger: {
        mode: "recurring",
        ...value.trigger,
        recurrence: { ...recurrence, ...patch },
      },
    });
  };

  const needsWeekday = recurrence.kind === "biweekly" || recurrence.kind === "weekly";

  return (
    <div className="space-y-3">
      <Field label="반복 주기">
        <Select
          value={recurrence.kind}
          onChange={(e) => updateRecurrence({ kind: e.target.value as Recurrence["kind"] })}
        >
          {RECURRENCE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      </Field>

      {needsWeekday ? (
        <Field label="요일">
          <Select
            value={recurrence.weekday ?? 6}
            onChange={(e) => updateRecurrence({ weekday: Number(e.target.value) })}
          >
            {WEEKDAYS.map((label, index) => (
              <option key={label} value={index}>
                {label}요일
              </option>
            ))}
          </Select>
        </Field>
      ) : null}

      {recurrence.kind === "biweekly" ? (
        <Field label="기준 날짜 (앵커)" hint="이 날짜를 기준으로 2주 간격을 계산합니다. 비우면 가장 가까운 요일을 사용합니다.">
          <TextInput
            type="date"
            value={recurrence.anchorDate ?? ""}
            onChange={(e) => updateRecurrence({ anchorDate: e.target.value || undefined })}
          />
        </Field>
      ) : null}

      <div className="grid grid-cols-2 gap-2">
        <Field label="알림 시각">
          <TimeInput value={recurrence.time ?? "08:00"} onChange={(e) => updateRecurrence({ time: e.target.value })} />
        </Field>
        <Field label="시간대(tz)">
          <TextInput value={recurrence.tz ?? SEOUL_TZ} onChange={(e) => updateRecurrence({ tz: e.target.value })} />
        </Field>
      </div>
    </div>
  );
}
