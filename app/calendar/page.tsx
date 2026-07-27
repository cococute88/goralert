"use client";

// GORALERT-ALERT-SYSTEM Layer B2 (REQ-043 / REQ-047)
// 기존 Gorani 캘린더 데이터와 identity 계약은 읽기 전용으로 재사용한다.
// ⭐/❤️ 메타데이터와 활성 알림 규칙에서 파생한 🔔 상태를 읽기 전용으로 표시한다.

import { useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertRule } from "@/lib/alerts/types";
import { watchAlertRules } from "@/lib/alerts/repositories";
import { loadCalendarDisplaySnapshot } from "@/lib/calendar-reader";
import { calendarIdentityKeys } from "@/lib/calendar-contract";
import {
  buildSingleCalendarEventDraft,
  deriveAlertedCalendarEventIds,
  isCalendarEventDatePast,
} from "@/lib/calendar-alerts";
import CalendarMonthView, { type CalendarViewEvent } from "@/components/calendar/CalendarMonthView";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import { stashDraft } from "@/components/alerts/draftStore";
import { useToast } from "@/components/alerts/ui/toast";

export default function GoralertCalendarPage() {
  const router = useRouter();
  const toast = useToast();
  const { user, loading: authLoading } = useFirebaseAuth();
  const [events, setEvents] = useState<CalendarViewEvent[]>([]);
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);

    loadCalendarDisplaySnapshot(user.uid)
      .then(({ events: calendarEvents, portfolioId }) => {
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
          portfolioId,
          sourceKind: item.sourceKind,
          identityKeys: calendarIdentityKeys(item as unknown as Record<string, unknown>),
        }));
        setEvents(derived);
      })
      .catch(() => {
        if (!active) return;
        setEvents([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    const unsubscribe = watchAlertRules(
      user.uid,
      (nextRules) => {
        if (active) setRules(nextRules);
      },
      () => {
        if (active) setRules([]);
      },
    );
    return () => {
      active = false;
      unsubscribe();
    };
  }, [user]);

  const alertedEventIds = useMemo(
    () => deriveAlertedCalendarEventIds(events, rules),
    [events, rules],
  );

  const handleCreateAlert = (event: CalendarViewEvent) => {
    const draft = buildSingleCalendarEventDraft(event);
    if (isCalendarEventDatePast(event.date)) {
      toast.error("이미 지난 일정에는 새 알림을 만들 수 없습니다");
      return;
    }
    stashDraft(draft);
    const returnTo = `/calendar?date=${encodeURIComponent(event.date)}`;
    router.push(`/alerts/new?mode=single-event&returnTo=${encodeURIComponent(returnTo)}`);
  };

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;

  return (
    <CalendarMonthView
      events={events}
      alertedEventIds={alertedEventIds}
      onCreateAlert={handleCreateAlert}
    />
  );
}
