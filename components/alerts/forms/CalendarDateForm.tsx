"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (US-006/007)
// 투자 캘린더 기반 알림 폼. 캘린더 이벤트(별⭐/하트❤️ 표시)에 맞춰 지정한 시각에
// 발송한다. condition.kind === "date" + selector, trigger.recurrence.kind === "calendar".

import type { CalendarMark, DateCondition, DateEventSelector } from "@/lib/alerts/types";
import {
  CALENDAR_EVENT_TYPE_OPTIONS,
  isKnownCalendarEventType,
  normalizeCalendarEventTypes,
  type AlertCalendarEventType,
} from "@/lib/alerts/calendar-event-types";
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
  const storedEventTypes = normalizeCalendarEventTypes(selector.match?.type);
  const selectedEventTypes = storedEventTypes.filter(isKnownCalendarEventType);
  // Preserve a value an older rule stored but the current UI does not know. It
  // remains visible and is not removed merely because another field is edited.
  const unknownEventTypes = storedEventTypes.filter((eventType) => !isKnownCalendarEventType(eventType));

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
    const next = { ...selector.match, ...patch };
    if (typeof next.titleContains === "string") {
      const trimmed = next.titleContains.trim();
      if (trimmed) next.titleContains = trimmed;
      else delete next.titleContains;
    }
    if (Array.isArray(next.type) && next.type.length === 0) delete next.type;
    updateSelector({ match: Object.keys(next).length ? next : undefined });
  };

  const toggleEventType = (eventType: AlertCalendarEventType) => {
    const next = selectedEventTypes.includes(eventType)
      ? selectedEventTypes.filter((item) => item !== eventType)
      : [...selectedEventTypes, eventType];
    const preserved = [...next, ...unknownEventTypes];
    updateMatch({ type: preserved.length ? preserved : undefined });
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
                aria-pressed={active}
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

      <Field label="이벤트 종류" hint="복수 선택할 수 있습니다. 선택하지 않으면 모든 일정 종류가 대상입니다.">
        <div className="flex flex-wrap gap-2">
          {CALENDAR_EVENT_TYPE_OPTIONS.map((option) => {
            const checked = selectedEventTypes.includes(option.value);
            return (
              <label
                key={option.value}
                className={`flex min-h-11 cursor-pointer items-center gap-2 rounded-xl border px-3 py-2 text-sm transition-colors ${
                  checked ? "border-accent bg-accent/10 text-foreground" : "border-border bg-background text-muted-foreground"
                }`}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  onChange={() => toggleEventType(option.value)}
                  className="size-4 accent-accent"
                />
                {option.label}
              </label>
            );
          })}
          {unknownEventTypes.map((eventType) => (
            <span key={eventType} className="flex min-h-11 items-center rounded-xl border border-warning/50 bg-warning/10 px-3 py-2 text-sm text-foreground">
              기존 종류: {eventType}
            </span>
          ))}
        </div>
      </Field>

      <Field
        label="포함 단어"
        hint="입력한 단어가 일정 제목에 포함된 경우만 알림을 보냅니다. 비워두면 모든 일정이 대상입니다. 대소문자를 구분하지 않습니다."
      >
        <TextInput
          value={selector.match?.titleContains ?? ""}
          placeholder="비워두면 전체"
          onChange={(e) => updateMatch({ titleContains: e.target.value })}
        />
      </Field>

      <Field label="알림 시각">
        <TimeInput value={recurrence?.time ?? "18:00"} onChange={(e) => updateTime(e.target.value)} />
      </Field>
    </div>
  );
}
