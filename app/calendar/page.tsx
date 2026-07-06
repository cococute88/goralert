"use client";

// GORALERT-ALERT-SYSTEM Layer B2 (REQ-043 / REQ-047)
// 캘린더 탭. 기존 Gorani 캘린더 데이터를 읽기 전용으로 재사용한다.
//   - loadLegacyImportedCalendarEvents + loadCalendarCustomEvents 로 일정을 모으고
//   - loadCalendarEventMetas 의 ⭐(star)/❤️(heart) 표시를 읽기 전용으로 붙인다.
//   - 🔔(bell) 표시만 고라알림 소유 컬렉션(users/{uid}/calendarAlertMarks)에 쓴다.
// ⭐/❤️ 및 기존 캘린더 컬렉션에는 절대 쓰지 않는다 (READ-ONLY).
//
// REQ-043 재사용: 기존 Gorani 캘린더 빌딩블록을 수정 없이 import 하여 재사용한다.
//   - lib/calendar-grid.ts (buildMonthGrid/formatIsoDate) → 월별 미니 그리드 개요
//   - lib/event-visuals.ts (getEventVisual/EVENT_VISUALS/eventChipLabel) → 종류 라벨/색상
// 풀 캘린더 컴포넌트(components/calendar/*)는 존재하지 않아, 위 lib 헬퍼를 재사용해
// 읽기 전용 월/날짜 그룹 뷰를 더 보기 좋게 렌더링한다. 기존 파일은 수정하지 않는다.

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { Bell, BellRing, CalendarDays, Plus } from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertRule, CalendarAlertMark } from "@/lib/alerts/types";
import {
  deleteCalendarAlertMark,
  loadCalendarAlertMarks,
  saveCalendarAlertMark,
} from "@/lib/alerts/repositories";
import {
  loadCalendarCustomEvents,
  loadCalendarEventMetas,
  loadLegacyImportedCalendarEvents,
} from "@/lib/calendar-reader";
import type { CalendarEventMeta } from "@/lib/calendar-types";
import type { CalendarEventType } from "@/lib/calendar-types";
import { buildMonthGrid, formatIsoDate } from "@/lib/calendar-grid";
import { getEventVisual, EVENT_VISUALS } from "@/lib/event-visuals";
import { Badge, Button, Card, CardSection, EmptyState, cx } from "@/components/alerts/ui";
import { useToast } from "@/components/alerts/ui/toast";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import { stashDraft } from "@/components/alerts/draftStore";

// 단일 화면에서 다루는 정규화된 캘린더 항목.
type DerivedCalendarEvent = {
  eventId: string;
  date: string; // YYYY-MM-DD
  ticker: string;
  type: CalendarEventType | string;
  title: string;
  star: boolean; // ⭐ READ-ONLY (event meta)
  heart: boolean; // ❤️ READ-ONLY (event meta)
};

const TYPE_LABELS: Record<string, string> = {
  ex_div: "배당락",
  buy_by: "매수 마감",
  pay: "배당 지급",
  earnings: "실적 발표",
  custom: "사용자 일정",
};

// 종류 라벨은 기존 event-visuals 의 한국어 라벨(getEventVisual().ko)을 재사용한다.
// 알 수 없는 타입은 원문 그대로 노출(기존 동작 유지).
function typeLabel(type: string): string {
  return type in EVENT_VISUALS ? getEventVisual(type).ko : TYPE_LABELS[type] ?? type;
}

// 종류 색상 클래스(배경/테두리/텍스트)도 event-visuals 에서 재사용한다.
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

// ⭐/❤️ 표시는 event meta(eventId 기준)에서 읽기 전용으로 가져온다.
function metaMark(meta: CalendarEventMeta | undefined): { star: boolean; heart: boolean } {
  return { star: Boolean(meta?.star), heart: Boolean(meta?.heart) };
}

// 캘린더 항목 → 날짜 기반(date) 새 알림 draft. new 페이지가 takeDraft 로 소비한다.
function buildCalendarDraft(event: DerivedCalendarEvent): Partial<AlertRule> {
  const label = typeLabel(event.type);
  const name = `${event.ticker} ${label}`.trim();
  return {
    kind: "date",
    name,
    enabled: true,
    condition: {
      kind: "date",
      selector: {
        source: "calendarEvents",
        match: {
          ...(event.ticker ? { ticker: event.ticker } : {}),
          ...(event.type ? { type: String(event.type) } : {}),
        },
        markFilter: ["star", "heart"],
      },
    },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "calendar", time: "09:00", tz: "Asia/Seoul" },
    },
    delivery: {
      channels: ["telegram", "push"],
      message: {
        title: name || "캘린더 알림",
        body: `${event.ticker} ${label} (${event.date}) 알림입니다`,
      },
    },
  };
}

function EventRow({
  event,
  marked,
  busy,
  onToggleBell,
  onCreateAlert,
}: {
  event: DerivedCalendarEvent;
  marked: boolean;
  busy: boolean;
  onToggleBell: () => void;
  onCreateAlert: () => void;
}) {
  return (
    <Card>
      <CardSection className="space-y-2 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">
                {event.ticker || "—"}
              </span>
              <span className={cx("inline-flex items-center rounded-full px-2 py-0.5 text-[11px] font-medium", typeVisualClass(event.type))}>
                {typeLabel(event.type)}
              </span>
            </div>
            {event.title ? (
              <p className="mt-0.5 truncate text-xs text-muted-foreground">{event.title}</p>
            ) : null}
          </div>
          <div className="flex items-center gap-1.5 text-base leading-none">
            {/* ⭐/❤️ 는 기존 캘린더 메타에서 읽기 전용 표시 */}
            <span aria-label="별표" title="별표 (읽기 전용)" className={event.star ? "" : "opacity-25 grayscale"}>
              ⭐
            </span>
            <span aria-label="하트" title="하트 (읽기 전용)" className={event.heart ? "" : "opacity-25 grayscale"}>
              ❤️
            </span>
            {/* 🔔 는 고라알림 소유 — 토글 가능 */}
            <button
              type="button"
              onClick={onToggleBell}
              disabled={busy}
              aria-pressed={marked}
              aria-label={marked ? "알림 표시 해제" : "알림 표시"}
              title={marked ? "알림 표시됨 (탭하여 해제)" : "알림 표시 추가"}
              className={`rounded-lg p-1 transition-colors disabled:opacity-50 ${
                marked ? "text-accent" : "text-muted-foreground hover:text-foreground"
              }`}
            >
              {marked ? <BellRing size={18} /> : <Bell size={18} />}
            </button>
          </div>
        </div>
        <Button size="sm" variant="secondary" className="w-full" onClick={onCreateAlert}>
          <Plus size={14} />이 항목으로 알림 만들기
        </Button>
      </CardSection>
    </Card>
  );
}

// 월별 미니 그리드 개요. lib/calendar-grid.ts(buildMonthGrid/formatIsoDate)를
// 그대로 재사용하여 일정이 있는 날을 점으로 표시한다(읽기 전용).
const WEEKDAY_HEADERS = ["일", "월", "화", "수", "목", "금", "토"];

function MonthGridOverview({
  monthDate,
  eventIso,
  bellIso,
}: {
  monthDate: Date;
  eventIso: Set<string>;
  bellIso: Set<string>;
}) {
  const cells = buildMonthGrid(monthDate);
  const todayIso = formatIsoDate(new Date());
  return (
    <div className="rounded-xl border border-border bg-card p-2">
      <div className="mb-1 grid grid-cols-7 gap-1 text-center text-[10px] font-medium text-muted-foreground">
        {WEEKDAY_HEADERS.map((header) => (
          <div key={header}>{header}</div>
        ))}
      </div>
      <div className="grid grid-cols-7 gap-1">
        {cells.map((cell) => {
          const has = eventIso.has(cell.isoDate);
          const bell = bellIso.has(cell.isoDate);
          const isToday = cell.isoDate === todayIso;
          return (
            <div
              key={cell.isoDate}
              className={cx(
                "relative flex h-7 items-center justify-center rounded text-[11px]",
                cell.isCurrentMonth ? "text-foreground" : "text-muted-foreground/40",
                has ? "font-semibold" : "",
                isToday ? "ring-1 ring-accent" : "",
              )}
            >
              {cell.day}
              {has ? (
                <span
                  className={cx(
                    "absolute bottom-0.5 h-1 w-1 rounded-full",
                    bell ? "bg-accent" : "bg-muted-foreground",
                  )}
                />
              ) : null}
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function GoralertCalendarPage() {
  const router = useRouter();
  const toast = useToast();
  const { user, loading: authLoading } = useFirebaseAuth();

  const [events, setEvents] = useState<DerivedCalendarEvent[]>([]);
  const [bellIds, setBellIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);

    Promise.all([
      loadLegacyImportedCalendarEvents(user.uid),
      loadCalendarCustomEvents(user.uid),
      loadCalendarEventMetas(user.uid),
      loadCalendarAlertMarks(user.uid),
    ])
      .then(([legacy, custom, metas, marks]) => {
        if (!active) return;

        const metaById = new Map<string, CalendarEventMeta>();
        for (const meta of metas) {
          if (meta?.eventId) metaById.set(meta.eventId, meta);
        }

        const derived: DerivedCalendarEvent[] = [];

        for (const item of legacy) {
          const { star, heart } = metaMark(metaById.get(item.id));
          derived.push({
            eventId: item.id,
            date: item.date,
            ticker: item.ticker,
            type: item.type,
            title: item.title ?? "",
            star,
            heart,
          });
        }

        for (const item of custom) {
          const { star, heart } = metaMark(metaById.get(item.id));
          derived.push({
            eventId: item.id,
            date: item.date,
            ticker: item.ticker ?? "",
            type: item.type,
            title: item.title,
            star,
            heart,
          });
        }

        derived.sort(
          (a, b) =>
            a.date.localeCompare(b.date) ||
            a.ticker.localeCompare(b.ticker) ||
            String(a.type).localeCompare(String(b.type)),
        );

        const ids = new Set<string>();
        for (const mark of marks) {
          if (mark.eventId) ids.add(mark.eventId);
        }

        setEvents(derived);
        setBellIds(ids);
      })
      .catch(() => {
        if (!active) return;
        setEvents([]);
        setBellIds(new Set());
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [user]);

  type MonthGroup = {
    monthKey: string; // YYYY-MM
    monthDate: Date;
    label: string;
    dates: Array<[string, DerivedCalendarEvent[]]>;
    eventIso: Set<string>;
  };

  const monthGroups = useMemo(() => {
    const byDate = new Map<string, DerivedCalendarEvent[]>();
    for (const event of events) {
      const list = byDate.get(event.date) ?? [];
      list.push(event);
      byDate.set(event.date, list);
    }
    const sortedDates = Array.from(byDate.entries()).sort((a, b) => a[0].localeCompare(b[0]));

    const byMonth = new Map<string, MonthGroup>();
    for (const [date, items] of sortedDates) {
      const monthKey = date.slice(0, 7);
      let group = byMonth.get(monthKey);
      if (!group) {
        const [year, month] = monthKey.split("-").map(Number);
        group = {
          monthKey,
          monthDate: new Date(year, (month || 1) - 1, 1),
          label: `${year}년 ${month}월`,
          dates: [],
          eventIso: new Set<string>(),
        };
        byMonth.set(monthKey, group);
      }
      group.dates.push([date, items]);
      group.eventIso.add(date);
    }
    return Array.from(byMonth.values()).sort((a, b) => a.monthKey.localeCompare(b.monthKey));
  }, [events]);

  // 🔔 표시가 있는 날짜 집합(월별 미니 그리드 강조용).
  const bellDates = useMemo(() => {
    const set = new Set<string>();
    for (const event of events) {
      if (bellIds.has(event.eventId)) set.add(event.date);
    }
    return set;
  }, [events, bellIds]);

  const handleToggleBell = async (event: DerivedCalendarEvent) => {
    if (!user) return;
    const marked = bellIds.has(event.eventId);
    setBusyId(event.eventId);

    // 낙관적 업데이트
    const nextIds = new Set(bellIds);
    if (marked) nextIds.delete(event.eventId);
    else nextIds.add(event.eventId);
    setBellIds(nextIds);

    try {
      if (marked) {
        await deleteCalendarAlertMark(user.uid, event.eventId);
        toast.success("알림 표시를 해제했어요");
      } else {
        const mark: CalendarAlertMark = {
          id: event.eventId,
          eventId: event.eventId,
          markType: "bell",
          ...(event.ticker ? { ticker: event.ticker } : {}),
          ...(event.date ? { date: event.date } : {}),
        };
        await saveCalendarAlertMark(user.uid, mark);
        toast.success("알림 표시를 추가했어요");
      }
    } catch (err) {
      // 롤백
      setBellIds(bellIds);
      toast.error(err instanceof Error ? err.message : "표시 변경에 실패했습니다");
    } finally {
      setBusyId(null);
    }
  };

  const handleCreateAlert = (event: DerivedCalendarEvent) => {
    stashDraft(buildCalendarDraft(event));
    router.push("/alerts/new");
  };

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-foreground">캘린더</h1>
        <Badge>{events.length}</Badge>
      </div>

      <p className="text-xs text-muted-foreground">
        ⭐ 별표 · ❤️ 하트는 기존 캘린더 표시(읽기 전용)이고, 🔔 알림 표시만 고라알림에서 관리합니다.
      </p>

      {monthGroups.length === 0 ? (
        <EmptyState
          icon={<CalendarDays size={28} />}
          title="표시할 캘린더 일정이 없어요"
          description="기존 Gorani 캘린더에 일정이 등록되면 여기에 표시됩니다."
        />
      ) : (
        <div className="space-y-6">
          {monthGroups.map((group) => (
            <section key={group.monthKey} className="space-y-3">
              <h2 className="text-sm font-bold text-foreground">{group.label}</h2>
              <MonthGridOverview monthDate={group.monthDate} eventIso={group.eventIso} bellIso={bellDates} />
              <div className="space-y-5">
                {group.dates.map(([date, items]) => (
                  <div key={date}>
                    <h3 className="mb-2 text-xs font-semibold text-muted-foreground">{formatDateHeader(date)}</h3>
                    <div className="space-y-2">
                      {items.map((event) => (
                        <EventRow
                          key={event.eventId}
                          event={event}
                          marked={bellIds.has(event.eventId)}
                          busy={busyId === event.eventId}
                          onToggleBell={() => handleToggleBell(event)}
                          onCreateAlert={() => handleCreateAlert(event)}
                        />
                      ))}
                    </div>
                  </div>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}
    </div>
  );
}
