"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-041)
// 홈 대시보드. AlertRules + 최근 NotificationLogs 를 읽어 3개 섹션을 보여준다.
// 읽기 전용 — 여기서 평가/발송은 하지 않는다.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Bell, CalendarClock, Plus, Send } from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertRule, NotificationLog } from "@/lib/alerts/types";
import type { ResolvedCalendarEvent } from "@/lib/calendar-types";
import { loadResolvedCalendarEvents } from "@/lib/calendar-reader";
import { loadAlertRules, loadNotificationLogs } from "@/lib/alerts/repositories";
import { formatNextOccurrence, nextRuleOccurrence } from "@/lib/alerts/schedule";
import { Badge, Button, Card, CardSection, EmptyState } from "@/components/alerts/ui";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import AlertKindBadge from "@/components/alerts/forms/AlertKindBadge";

type RuleWithNext = { rule: AlertRule; next: Date | null };

function isSameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function SectionTitle({ icon, title, count }: { icon: React.ReactNode; title: string; count?: number }) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <span className="text-accent">{icon}</span>
      <h2 className="text-sm font-semibold text-foreground">{title}</h2>
      {typeof count === "number" ? <Badge>{count}</Badge> : null}
    </div>
  );
}

function RuleRow({ rule, next }: RuleWithNext) {
  return (
    <Link href={`/alerts/${rule.id}`} className="block">
      <Card className="transition-colors hover:border-accent">
        <CardSection className="flex items-center justify-between gap-2 py-3">
          <span className="min-w-0">
            <span className="flex items-center gap-2">
              <span className="truncate text-sm font-medium text-foreground">{rule.name}</span>
              <AlertKindBadge kind={rule.kind} />
            </span>
            <span className="mt-0.5 block text-xs text-muted-foreground">
              {next ? formatNextOccurrence(next) : "해당 조건의 예정 일정 없음"}
            </span>
          </span>
        </CardSection>
      </Card>
    </Link>
  );
}

export default function GoralertHome() {
  const { user, loading: authLoading } = useFirebaseAuth();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [logs, setLogs] = useState<NotificationLog[]>([]);
  const [calendarEvents, setCalendarEvents] = useState<ResolvedCalendarEvent[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);
    Promise.allSettled([
      loadAlertRules(user.uid),
      loadNotificationLogs(user.uid, { limit: 5 }),
      loadResolvedCalendarEvents(user.uid),
    ])
      .then(([rulesResult, logsResult, calendarResult]) => {
        if (!active) return;
        setRules(rulesResult.status === "fulfilled" ? rulesResult.value : []);
        setLogs(logsResult.status === "fulfilled" ? logsResult.value : []);
        setCalendarEvents(calendarResult.status === "fulfilled" ? calendarResult.value : []);
        if (calendarResult.status === "rejected") {
          console.error("[calendar-contract] dashboard read failed", calendarResult.reason);
        }
      })
      .catch(() => {
        if (!active) return;
        setRules([]);
        setLogs([]);
        setCalendarEvents([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user]);

  const { todayRules, upcomingRules } = useMemo(() => {
    const now = new Date();
    const enabled = rules.filter((rule) => rule.enabled);
    const withNext: RuleWithNext[] = enabled.map((rule) => ({
      rule,
      next: nextRuleOccurrence(rule, calendarEvents, now),
    }));

    // "오늘 예정" = 다음 발송 시각이 오늘로 계산되는 (예측 가능한) 룰만.
    const today = withNext.filter((item) => item.next !== null && isSameDay(item.next, now));

    // "다음 예정"은 고정 반복과 실제 resolve된 캘린더 일정만 포함한다.
    const scheduledUpcoming = withNext
      .filter((item) => item.next && !isSameDay(item.next, now) && item.next.getTime() > now.getTime())
      .sort((a, b) => a.next!.getTime() - b.next!.getTime())
      .slice(0, 5);
    return { todayRules: today, upcomingRules: scheduledUpcoming };
  }, [calendarEvents, rules]);

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;

  // First-time user (no rules, no logs): one welcoming onboarding card with a
  // single clear next action, instead of three repetitive empty-state cards.
  if (rules.length === 0 && logs.length === 0) {
    return (
      <div className="space-y-6">
        <h1 className="text-lg font-bold text-foreground">대시보드</h1>
        <EmptyState
          icon={<Bell size={28} />}
          title="첫 알림을 만들어볼까요?"
          description="날짜·전환비·RSI·캘린더 조건으로 투자 알림을 받을 수 있어요. 템플릿을 고르면 30초 만에 시작할 수 있습니다."
          action={
            <Link href="/alerts/new">
              <Button>
                <Plus size={16} />새 알림 만들기
              </Button>
            </Link>
          }
        />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-foreground">대시보드</h1>
        <Link href="/alerts/new">
          <Button size="sm">
            <Plus size={16} />새 알림
          </Button>
        </Link>
      </div>

      <section>
        <SectionTitle icon={<CalendarClock size={16} />} title="오늘 예정 알림" count={todayRules.length} />
        {todayRules.length === 0 ? (
          <EmptyState title="오늘 예정된 알림이 없어요" description="새 알림을 만들어 일정을 관리해보세요." />
        ) : (
          <div className="space-y-2">
            {todayRules.map((item) => (
              <RuleRow key={item.rule.id} rule={item.rule} next={item.next} />
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle icon={<Send size={16} />} title="최근 발송 알림" count={logs.length} />
        {logs.length === 0 ? (
          <EmptyState title="발송된 알림이 없어요" description="알림이 발송되면 여기에 기록이 표시됩니다." />
        ) : (
          <div className="space-y-2">
            {logs.map((log) => (
              <Card key={log.id}>
                <CardSection className="py-3">
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-sm font-medium text-foreground">
                      {log.ruleName ?? log.message.title}
                    </span>
                    <div className="flex items-center gap-1">
                      {log.isTest ? <Badge tone="warning">테스트</Badge> : null}
                      <AlertKindBadge kind={log.kind} />
                    </div>
                  </div>
                  <p className="mt-0.5 truncate text-xs text-muted-foreground">{log.message.body}</p>
                  <p className="mt-1 text-[11px] text-muted-foreground">
                    {new Date(log.firedAt).toLocaleString("ko-KR")}
                  </p>
                </CardSection>
              </Card>
            ))}
          </div>
        )}
      </section>

      <section>
        <SectionTitle icon={<Bell size={16} />} title="다음 예정 알림" count={upcomingRules.length} />
        {upcomingRules.length === 0 ? (
          <EmptyState title="예정된 알림이 없어요" description="반복 일정을 설정하면 다음 발송 예정이 표시됩니다." />
        ) : (
          <div className="space-y-2">
            {upcomingRules.map((item) => (
              <RuleRow key={item.rule.id} rule={item.rule} next={item.next} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
