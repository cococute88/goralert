"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (US-006/007)
// 투자 캘린더 기반 알림 폼. 캘린더 이벤트(별⭐/하트❤️ 표시)에 맞춰 지정한 시각에
// 발송한다. condition.kind === "date" + selector, trigger.recurrence.kind === "calendar".

import type { CalendarMark, DateCondition, DateEventSelector } from "@/lib/alerts/types";
import { Field, Select, TimeInput, TextInput, SEOUL_TZ, type RuleFormProps } from "./fields";

const SOURCE_OPTIONS: { value: DateEventSelector["source"]; label: string }[] = [
  { value: "calendarEvents", label: "투자 캘린더 (기본)" },
  { value: "calendarCustomEvents", label: "내 사용자 일정" },
];

function asDateCondition(value: Partial<DateCondition> | undefined): DateCondition {
  return {
    kind: "date",
    selector: value?.selector ?? { source: "calendarEvents", markFilter: ["star", "heart"] },
  };
}

export default function CalendarDateForm({ value, onChange }: RuleFormProps) {
  const condition = asDateCondition(value.condition?.kind === "date" ? value.condition : undefined);
  const selector = condition.selector ?? { source: "calendarEvents" };
  const marks = selector.markFilter ?? [];
  const recurrence = value.trigger?.recurrence;

  const updateSelector = (patch: Partial<DateEventSelector>) => {
    onChange({
      ...value,
      condition: { kind: "date", selector: { ...selector, ...patch } },
      trigger: {
        mode: "recurring",
        ...value.trigger,
        recurrence: { kind: "calendar", tz: SEOUL_TZ, time: recurrence?.time ?? "18:00", ...recurrence },
      },
    });
  };

  const updateMatch = (patch: Partial<NonNullable<DateEventSelector["match"]>>) => {
    updateSelector({ match: { ...selector.match, ...patch } });
  };

  const toggleMark = (mark: CalendarMark) => {
    const next = marks.includes(mark) ? marks.filter((m) => m !== mark) : [...marks, mark];
    updateSelector({ markFilter: next });
  };

  const updateTime = (time: string) => {
    onChange({
      ...value,
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
      <Field label="캘린더 소스">
        <Select value={selector.source} onChange={(e) => updateSelector({ source: e.target.value as DateEventSelector["source"] })}>
          {SOURCE_OPTIONS.map((opt) => (
            <option key={opt.value} value={opt.value}>
              {opt.label}
            </option>
          ))}
        </Select>
      </Field>

      <Field label="표시 필터" hint="선택한 표시가 있는 종목만 알림 대상이 됩니다.">
        <div className="flex gap-2">
          {(["star", "heart"] as CalendarMark[]).map((mark) => {
            const active = marks.includes(mark);
            return (
              <button
                key={mark}
                type="button"
                onClick={() => toggleMark(mark)}
                className={`flex-1 rounded-xl border px-3 py-2 text-sm ${
                  active ? "border-accent bg-accent/10 text-foreground" : "border-border bg-background text-muted-foreground"
                }`}
              >
                {mark === "star" ? "⭐ 별표" : "❤️ 하트"}
              </button>
            );
          })}
        </div>
      </Field>

      <div className="grid grid-cols-2 gap-2">
        <Field label="이벤트 종류" hint="예: ex-dividend, buy-deadline">
          <TextInput
            value={selector.match?.type ?? ""}
            placeholder="비우면 전체"
            onChange={(e) => updateMatch({ type: e.target.value || undefined })}
          />
        </Field>
        <Field label="제목 포함">
          <TextInput
            value={selector.match?.titleContains ?? ""}
            placeholder="비우면 전체"
            onChange={(e) => updateMatch({ titleContains: e.target.value || undefined })}
          />
        </Field>
      </div>

      <Field label="알림 시각">
        <TimeInput value={recurrence?.time ?? "18:00"} onChange={(e) => updateTime(e.target.value)} />
      </Field>
    </div>
  );
}
