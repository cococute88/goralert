"use client";

import { BellRing, CalendarDays } from "lucide-react";
import type { DateCondition } from "@/lib/alerts/types";
import { calendarEventTypeName } from "@/lib/calendar-alerts";
import { Card, CardSection } from "@/components/alerts/ui";
import { Field, TimeInput, type RuleFormProps } from "./fields";

export default function SingleCalendarEventForm({ value, onChange }: RuleFormProps) {
  const condition = value.condition as DateCondition | undefined;
  const selector = condition?.selector;
  const match = selector?.match ?? {};
  const rawType = Array.isArray(match.type) ? match.type[0] : match.type;
  const sourceLabel = selector?.source === "calendarCustomEvents" ? "내 사용자 일정" : "투자 캘린더";
  const target = match.ticker || match.titleContains || value.name || "선택한 일정";
  const time = value.trigger?.recurrence?.time ?? "09:00";

  const setTime = (nextTime: string) => {
    onChange({
      ...value,
      trigger: {
        ...(value.trigger ?? { mode: "once" }),
        mode: "once",
        recurrence: {
          kind: "calendar",
          time: nextTime,
          tz: value.trigger?.recurrence?.tz ?? "Asia/Seoul",
        },
      },
    });
  };

  return (
    <div className="space-y-4">
      <Card>
        <CardSection className="space-y-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-foreground">
            <BellRing size={17} className="text-accent" />
            선택한 일정 한 건
          </div>
          <dl className="grid grid-cols-[5rem_minmax(0,1fr)] gap-x-3 gap-y-2 text-xs">
            <dt className="text-muted-foreground">일정</dt>
            <dd className="truncate font-medium text-foreground" title={String(target)}>{target}</dd>
            <dt className="text-muted-foreground">날짜</dt>
            <dd className="flex items-center gap-1 font-medium text-foreground">
              <CalendarDays size={13} />
              {match.date || "날짜 없음"}
            </dd>
            <dt className="text-muted-foreground">캘린더</dt>
            <dd className="font-medium text-foreground">{sourceLabel}</dd>
            <dt className="text-muted-foreground">이벤트 종류</dt>
            <dd className="font-medium text-foreground">
              {rawType ? calendarEventTypeName(rawType) : "선택한 일정"}
            </dd>
          </dl>
        </CardSection>
      </Card>

      <Field label="알림 시각" hint="선택한 일정 당일에 한 번 알립니다.">
        <TimeInput value={time} onChange={(event) => setTime(event.target.value)} />
      </Field>
    </div>
  );
}
