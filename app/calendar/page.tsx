"use client";

// GORALERT-ALERT-SYSTEM Layer B2 (REQ-043 / REQ-047)
// 기존 Gorani 캘린더 데이터와 identity 계약은 읽기 전용으로 재사용한다.
// ⭐/❤️ 메타데이터는 표시만 하고, 🔔만 users/{uid}/calendarAlertMarks에 쓴다.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertRule, CalendarAlertMark } from "@/lib/alerts/types";
import {
  deleteCalendarAlertMark,
  loadCalendarAlertMarks,
  saveCalendarAlertMark,
} from "@/lib/alerts/repositories";
import { loadCalendarDisplayEvents } from "@/lib/calendar-reader";
import {
  calendarIdentityKeys,
  findMatchingCalendarIdentityKey,
} from "@/lib/calendar-contract";
import CalendarMonthView, {
  calendarEventTypeLabel,
  type CalendarViewEvent,
} from "@/components/calendar/CalendarMonthView";
import { useToast } from "@/components/alerts/ui/toast";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import { stashDraft } from "@/components/alerts/draftStore";

function buildCalendarDraft(event: CalendarViewEvent): Partial<AlertRule> {
  const label = calendarEventTypeLabel(event.type);
  const name = event.type === "custom" ? event.title : `${event.ticker} ${label}`.trim();
  return {
    kind: "date",
    name,
    enabled: true,
    condition: {
      kind: "date",
      selector: {
        source: event.source,
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

export default function GoralertCalendarPage() {
  const router = useRouter();
  const toast = useToast();
  const { user, loading: authLoading } = useFirebaseAuth();
  const [events, setEvents] = useState<CalendarViewEvent[]>([]);
  const [bellIds, setBellIds] = useState<Set<string>>(new Set());
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<string | null>(null);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);

    Promise.all([
      loadCalendarDisplayEvents(user.uid),
      loadCalendarAlertMarks(user.uid),
    ])
      .then(([calendarEvents, marks]) => {
        if (!active) return;

        const derived: CalendarViewEvent[] = calendarEvents.map((item) => ({
          eventId: item.id,
          date: item.date,
          ticker: item.ticker,
          type: item.type,
          title: item.title ?? "",
          star: item.star,
          heart: item.heart,
          source: item.source,
          sourceKind: item.sourceKind,
          identityKeys: calendarIdentityKeys(item as unknown as Record<string, unknown>),
        }));

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

  const handleToggleBell = async (event: CalendarViewEvent) => {
    if (!user) return;
    const existingMarkId = findMatchingCalendarIdentityKey(event.identityKeys, bellIds);
    const marked = existingMarkId !== null;
    setBusyId(event.eventId);

    const nextIds = new Set(bellIds);
    if (existingMarkId) nextIds.delete(existingMarkId);
    else nextIds.add(event.eventId);
    setBellIds(nextIds);

    try {
      if (marked) {
        await deleteCalendarAlertMark(user.uid, existingMarkId);
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
      setBellIds(bellIds);
      toast.error(err instanceof Error ? err.message : "표시 변경에 실패했습니다");
    } finally {
      setBusyId(null);
    }
  };

  const handleCreateAlert = (event: CalendarViewEvent) => {
    stashDraft(buildCalendarDraft(event));
    router.push("/alerts/new");
  };

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;

  return (
    <CalendarMonthView
      events={events}
      bellIds={bellIds}
      busyId={busyId}
      onToggleBell={handleToggleBell}
      onCreateAlert={handleCreateAlert}
    />
  );
}
