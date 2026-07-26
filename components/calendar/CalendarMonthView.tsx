"use client";

import { useMemo, useState } from "react";
import {
  Bell,
  BellRing,
  CalendarDays,
  ChevronLeft,
  ChevronRight,
  Plus,
} from "lucide-react";
import type { CalendarEventType } from "@/lib/calendar-types";
import {
  findMatchingCalendarIdentityKey,
} from "@/lib/calendar-contract";
import {
  buildCalendarDayCellModel,
  isCustomCalendarDisplayEvent,
} from "@/lib/calendar-display";
import { buildMonthGrid, formatIsoDate } from "@/lib/calendar-grid";
import {
  addCalendarMonths,
  calendarEventsForDate,
  calendarEventsForMonth,
  groupCalendarEventsByDate,
  startOfCalendarMonth,
} from "@/lib/calendar-view";
import { getEventVisual, EVENT_VISUALS } from "@/lib/event-visuals";
import { Badge, Button, Card, CardSection, EmptyState, cx } from "@/components/alerts/ui";

export type CalendarViewEvent = {
  eventId: string;
  date: string;
  ticker: string;
  type: CalendarEventType | string;
  title: string;
  star: boolean;
  heart: boolean;
  source: "calendarEvents" | "calendarCustomEvents";
  sourceKind?: string;
  identityKeys: string[];
};

const TYPE_LABELS: Record<string, string> = {
  ex_div: "배당락",
  buy_by: "매수 마감",
  pay: "배당 지급",
  earnings: "실적 발표",
  custom: "사용자 일정",
};

export function calendarEventTypeLabel(type: string): string {
  return type in EVENT_VISUALS ? getEventVisual(type).ko : TYPE_LABELS[type] ?? type;
}

function typeVisualClass(type: string): string {
  const visual = getEventVisual(type);
  return cx(visual.bg, visual.border, visual.text, "border");
}

const DATE_HEADER = new Intl.DateTimeFormat("ko-KR", {
  year: "numeric",
  month: "long",
  day: "numeric",
  weekday: "short",
});

function formatDateHeader(date: string): string {
  const parsed = new Date(`${date}T00:00:00`);
  if (!Number.isFinite(parsed.getTime())) return date;
  return DATE_HEADER.format(parsed);
}

function formatMonthHeader(month: Date): string {
  return `${month.getFullYear()}년 ${month.getMonth() + 1}월`;
}

function EventRow({
  event,
  marked,
  busy,
  onToggleBell,
  onCreateAlert,
}: {
  event: CalendarViewEvent;
  marked: boolean;
  busy: boolean;
  onToggleBell: () => void;
  onCreateAlert: () => void;
}) {
  const custom = isCustomCalendarDisplayEvent(event);
  return (
    <Card>
      <CardSection className="space-y-2 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">
                {custom ? event.title : event.ticker || event.title}
              </span>
              <span
                className={cx(
                  "inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium",
                  typeVisualClass(event.type),
                )}
              >
                {calendarEventTypeLabel(event.type)}
              </span>
            </div>
            {event.title && !custom ? (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{event.title}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-1.5 text-base leading-none">
            <span aria-label="별표" title="별표 (읽기 전용)" className={event.star ? "" : "opacity-25 grayscale"}>
              ⭐
            </span>
            <span aria-label="하트" title="하트 (읽기 전용)" className={event.heart ? "" : "opacity-25 grayscale"}>
              ❤️
            </span>
            <button
              type="button"
              onClick={onToggleBell}
              disabled={busy}
              aria-pressed={marked}
              aria-label={marked ? "알림 표시 해제" : "알림 표시"}
              title={marked ? "알림 표시됨 (탭하여 해제)" : "알림 표시 추가"}
              className={cx(
                "rounded-lg p-1 transition-colors disabled:opacity-50",
                marked ? "text-accent" : "text-muted-foreground hover:text-foreground",
              )}
            >
              {marked ? <BellRing size={18} /> : <Bell size={18} />}
            </button>
          </div>
        </div>
        <Button size="sm" variant="secondary" className="w-full" onClick={onCreateAlert}>
          <Plus size={14} />
          이 항목으로 알림 만들기
        </Button>
      </CardSection>
    </Card>
  );
}

const WEEKDAY_HEADERS = ["일", "월", "화", "수", "목", "금", "토"];

function MonthCalendar({
  monthDate,
  eventsByDate,
  bellIds,
  selectedDate,
  onSelectDate,
}: {
  monthDate: Date;
  eventsByDate: Map<string, CalendarViewEvent[]>;
  bellIds: Set<string>;
  selectedDate: string;
  onSelectDate: (date: Date, isoDate: string, isCurrentMonth: boolean) => void;
}) {
  const cells = buildMonthGrid(monthDate);
  const todayIso = formatIsoDate(new Date());

  return (
    <div className="rounded-2xl border border-border bg-card p-2 shadow-sm">
      <div className="mb-1 grid grid-cols-7 text-center text-[11px] font-medium text-muted-foreground">
        {WEEKDAY_HEADERS.map((header) => (
          <div key={header} className="py-1">{header}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((cell) => {
          const dayEvents = cell.isCurrentMonth ? eventsByDate.get(cell.isoDate) ?? [] : [];
          const cellModel = buildCalendarDayCellModel(dayEvents);
          const firstCustom = cellModel.customEvents[0];
          const isSelected = cell.isoDate === selectedDate;
          const isToday = cell.isoDate === todayIso;
          const hasBell = dayEvents.some(
            (event) => findMatchingCalendarIdentityKey(event.identityKeys, bellIds) !== null,
          );

          return (
            <button
              key={cell.isoDate}
              type="button"
              aria-label={`${cell.year}년 ${cell.month}월 ${cell.day}일${dayEvents.length ? `, 일정 ${dayEvents.length}개` : ""}`}
              aria-pressed={isSelected}
              aria-current={isToday ? "date" : undefined}
              onClick={() => onSelectDate(cell.date, cell.isoDate, cell.isCurrentMonth)}
              className={cx(
                "relative flex h-[104px] min-w-0 flex-col items-stretch overflow-hidden rounded-lg border px-1 py-1 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent",
                cell.isCurrentMonth
                  ? "border-transparent text-foreground hover:bg-muted"
                  : "border-transparent bg-muted/20 text-muted-foreground/40",
                isToday && !isSelected ? "ring-1 ring-inset ring-accent/60" : "",
                isSelected ? "border-accent bg-accent/10 ring-1 ring-accent" : "",
              )}
            >
              <div className="flex h-5 min-w-0 shrink-0 items-center gap-0.5">
                <span
                  className={cx(
                    "shrink-0 text-[11px] font-bold leading-none",
                    hasBell ? "text-accent" : "",
                  )}
                >
                  {cell.day}
                </span>
                {firstCustom ? (
                  <span
                    className="min-w-0 flex-1 truncate text-[7.5px] font-semibold leading-none text-warning sm:text-[9px]"
                    title={cellModel.customEvents.map((event) => event.title).join(", ")}
                  >
                    {firstCustom.title}
                  </span>
                ) : null}
                {cellModel.customEvents.length > 1 ? (
                  <span className="shrink-0 text-[7px] font-semibold leading-none text-warning">
                    +{cellModel.customEvents.length - 1}
                  </span>
                ) : null}
              </div>
              <div className="mt-0.5 flex min-h-0 min-w-0 flex-1 flex-col gap-0.5 overflow-hidden">
                {cellModel.visibleRegularEvents.map((event) => (
                  <span
                    key={event.eventId}
                    title={`${event.ticker} ${calendarEventTypeLabel(event.type)}`.trim()}
                    className={cx(
                      "min-w-0 truncate rounded border px-0.5 py-0.5 text-center text-[7.5px] font-semibold leading-none tracking-tight sm:text-[9px]",
                      typeVisualClass(event.type),
                    )}
                  >
                    {event.star ? "⭐" : event.heart ? "♥" : ""}
                    {event.ticker || event.title}
                  </span>
                ))}
                {cellModel.regularOverflowCount > 0 ? (
                  <span className="shrink-0 text-center text-[7px] font-semibold leading-none text-muted-foreground sm:text-[8px]">
                    외 {cellModel.regularOverflowCount}개
                  </span>
                ) : null}
              </div>
            </button>
          );
        })}
      </div>
    </div>
  );
}

export default function CalendarMonthView({
  events,
  bellIds,
  busyId,
  onToggleBell,
  onCreateAlert,
}: {
  events: CalendarViewEvent[];
  bellIds: Set<string>;
  busyId: string | null;
  onToggleBell: (event: CalendarViewEvent) => void;
  onCreateAlert: (event: CalendarViewEvent) => void;
}) {
  const [initialToday] = useState(() => new Date());
  const [selectedMonth, setSelectedMonth] = useState(() => startOfCalendarMonth(initialToday));
  const [selectedDate, setSelectedDate] = useState(() => formatIsoDate(initialToday));

  const monthEvents = useMemo(
    () => calendarEventsForMonth(events, selectedMonth),
    [events, selectedMonth],
  );
  const eventsByDate = useMemo(
    () => new Map(groupCalendarEventsByDate(monthEvents)),
    [monthEvents],
  );
  const selectedDateEvents = useMemo(
    () => calendarEventsForDate(monthEvents, selectedDate),
    [monthEvents, selectedDate],
  );
  const monthDateGroups = useMemo(
    () => groupCalendarEventsByDate(monthEvents),
    [monthEvents],
  );

  const selectMonth = (month: Date, date: string) => {
    setSelectedMonth(startOfCalendarMonth(month));
    setSelectedDate(date);
  };

  const moveMonth = (offset: number) => {
    const nextMonth = addCalendarMonths(selectedMonth, offset);
    selectMonth(nextMonth, formatIsoDate(nextMonth));
  };

  const returnToCurrentMonth = () => {
    const today = new Date();
    selectMonth(today, formatIsoDate(today));
  };

  const handleSelectDate = (date: Date, isoDate: string, isCurrentMonth: boolean) => {
    setSelectedDate(isoDate);
    if (!isCurrentMonth) setSelectedMonth(startOfCalendarMonth(date));
  };

  const renderEvent = (event: CalendarViewEvent, keyPrefix: string) => (
    <EventRow
      key={`${keyPrefix}-${event.eventId}`}
      event={event}
      marked={findMatchingCalendarIdentityKey(event.identityKeys, bellIds) !== null}
      busy={busyId === event.eventId}
      onToggleBell={() => onToggleBell(event)}
      onCreateAlert={() => onCreateAlert(event)}
    />
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h1 className="text-lg font-bold text-foreground">캘린더</h1>
        <Badge tone="accent">이번 달 {monthEvents.length}개</Badge>
      </div>

      <p className="text-xs text-muted-foreground">
        ⭐ 별표 · ❤️ 하트는 기존 캘린더 표시(읽기 전용)이고, 🔔 알림 표시만 고라알림에서 관리합니다.
      </p>

      <section className="space-y-3" aria-labelledby="calendar-month-heading">
        <Card>
          <CardSection className="p-2">
            <div className="grid grid-cols-[2.5rem_minmax(0,1fr)_auto_2.5rem] items-center gap-1">
              <Button
                type="button"
                variant="ghost"
                className="px-0"
                aria-label="이전 달"
                onClick={() => moveMonth(-1)}
              >
                <ChevronLeft size={20} />
              </Button>
              <h2 id="calendar-month-heading" className="truncate text-center text-base font-bold text-foreground">
                {formatMonthHeader(selectedMonth)}
              </h2>
              <Button type="button" size="sm" variant="secondary" onClick={returnToCurrentMonth}>
                이번 달
              </Button>
              <Button
                type="button"
                variant="ghost"
                className="px-0"
                aria-label="다음 달"
                onClick={() => moveMonth(1)}
              >
                <ChevronRight size={20} />
              </Button>
            </div>
          </CardSection>
        </Card>

        <MonthCalendar
          monthDate={selectedMonth}
          eventsByDate={eventsByDate}
          bellIds={bellIds}
          selectedDate={selectedDate}
          onSelectDate={handleSelectDate}
        />
      </section>

      <section className="space-y-3" aria-labelledby="selected-date-heading">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-medium text-muted-foreground">선택 날짜 일정</p>
            <h2 id="selected-date-heading" className="text-sm font-bold text-foreground">
              {formatDateHeader(selectedDate)}
            </h2>
          </div>
          <Badge>{selectedDateEvents.length}개</Badge>
        </div>

        {selectedDateEvents.length > 0 ? (
          <div className="space-y-2">
            {selectedDateEvents.map((event) => renderEvent(event, "selected"))}
          </div>
        ) : (
          <EmptyState
            icon={<CalendarDays size={24} />}
            title="선택한 날짜에 일정이 없습니다."
          />
        )}
      </section>

      <section className="space-y-4" aria-labelledby="monthly-events-heading">
        <div className="flex items-center justify-between gap-2">
          <div>
            <p className="text-[11px] font-medium text-muted-foreground">이번 달 일정</p>
            <h2 id="monthly-events-heading" className="text-sm font-bold text-foreground">
              {formatMonthHeader(selectedMonth)}
            </h2>
          </div>
          <Badge>{monthEvents.length}개</Badge>
        </div>

        {monthDateGroups.length > 0 ? (
          <div className="space-y-5">
            {monthDateGroups.map(([date, items]) => (
              <div key={date}>
                <h3 className="mb-2 text-xs font-semibold text-muted-foreground">
                  {formatDateHeader(date)}
                </h3>
                <div className="space-y-2">
                  {items.map((event) => renderEvent(event, `month-${date}`))}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState
            icon={<CalendarDays size={28} />}
            title="이번 달 일정이 없습니다."
            description="다른 달로 이동해 예정된 일정을 확인해 보세요."
          />
        )}
      </section>
    </div>
  );
}
