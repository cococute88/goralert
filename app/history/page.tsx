"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B2 (REQ-044 / REQ-016)
// 발송 기록 탭. NotificationLog 를 검색/필터링한다.
//   - 자유 텍스트 검색 (예: "BXSL") — ruleName/tickers/message 대상 (REQ-024 client-side fallback)
//   - 필터 칩 9종: 전체 / 날짜 / 전환비 / RSI / 캘린더 / 사용자정의 / 발송성공 / 발송실패 / 기간
//   - 변경 시 searchNotificationLogs 호출(디바운스). 기본은 loadNotificationLogs 최근 윈도우.
//   - "더 보기"로 limit 증가.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ListChecks, Loader2, Search, X } from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertKind, DeliveryChannel, NotificationLog } from "@/lib/alerts/types";
import { loadNotificationLogs, searchNotificationLogs } from "@/lib/alerts/repositories";
import { Badge, Button, Card, CardSection, EmptyState, Toggle, cx } from "@/components/alerts/ui";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import { alertKindLabel } from "@/components/alerts/forms/AlertKindBadge";

const PAGE_SIZE = 25;

// 칩으로 노출되는 종류 필터.
type KindFilter = AlertKind | "all";
type StatusFilter = "sent" | "failed" | null;

const KIND_CHIPS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "전체" },
  { value: "date", label: "날짜 기반" },
  { value: "ratio", label: "전환비" },
  { value: "rsi", label: "RSI" },
  { value: "custom", label: "사용자 정의" },
];

const CHANNEL_LABELS: Record<DeliveryChannel, string> = {
  telegram: "Telegram",
  push: "Push",
};

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cx(
        "rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "border-accent bg-accent/10 text-accent"
          : "border-border bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {children}
    </button>
  );
}

function formatDateTime(iso: string | undefined): string {
  if (!iso) return "—";
  const parsed = new Date(iso);
  if (!Number.isFinite(parsed.getTime())) return iso;
  return parsed.toLocaleString("ko-KR");
}

// evaluatedAt → sentAt 지연(초) 힌트.
function delayHint(log: NotificationLog): string | null {
  if (!log.evaluatedAt || !log.sentAt) return null;
  const evaluated = new Date(log.evaluatedAt).getTime();
  const sent = new Date(log.sentAt).getTime();
  if (!Number.isFinite(evaluated) || !Number.isFinite(sent)) return null;
  const deltaSec = Math.max(0, Math.round((sent - evaluated) / 1000));
  return `지연 ${deltaSec}s`;
}

function LogRow({ log }: { log: NotificationLog }) {
  const delay = delayHint(log);
  return (
    <Card>
      <CardSection className="space-y-2 py-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-1.5">
              <span className="truncate text-sm font-semibold text-foreground">
                {log.ruleName ?? log.message.title}
              </span>
              <Badge tone="accent">{alertKindLabel(log.kind)}</Badge>
              {log.isTest ? <Badge tone="warning">테스트</Badge> : null}
            </div>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">{log.message.body}</p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          {log.channels.length === 0 ? (
            <Badge tone="neutral">채널 없음</Badge>
          ) : (
            log.channels.map((channel) => (
              <Badge
                key={channel.channel}
                tone={channel.status === "sent" ? "success" : "danger"}
              >
                {CHANNEL_LABELS[channel.channel] ?? channel.channel}{" "}
                {channel.status === "sent" ? "성공" : "실패"}
              </Badge>
            ))
          )}
          {log.evaluatedValue !== undefined ? (
            <Badge tone="neutral">값 {String(log.evaluatedValue)}</Badge>
          ) : null}
        </div>

        <div className="flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-muted-foreground">
          <span>발송 {formatDateTime(log.firedAt)}</span>
          {delay ? <span>{delay}</span> : null}
        </div>
      </CardSection>
    </Card>
  );
}

function HistorySkeleton() {
  return (
    <div className="space-y-2" aria-hidden>
      {Array.from({ length: 4 }).map((_, index) => (
        <Card key={index}>
          <CardSection className="space-y-2 py-3">
            <div className="h-4 w-2/3 animate-pulse rounded bg-muted" />
            <div className="h-3 w-1/2 animate-pulse rounded bg-muted" />
            <div className="flex gap-1.5">
              <div className="h-4 w-16 animate-pulse rounded-full bg-muted" />
              <div className="h-4 w-12 animate-pulse rounded-full bg-muted" />
            </div>
          </CardSection>
        </Card>
      ))}
    </div>
  );
}

export default function GoralertHistoryPage() {
  const { user, loading: authLoading } = useFirebaseAuth();

  const [text, setText] = useState("");
  const [kind, setKind] = useState<KindFilter>("all");
  const [status, setStatus] = useState<StatusFilter>(null);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [includeTest, setIncludeTest] = useState(true);
  const [limit, setLimit] = useState(PAGE_SIZE);

  const [logs, setLogs] = useState<NotificationLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [searching, setSearching] = useState(false);

  // 캘린더 종류는 date 로 매핑(캘린더 기반 알림은 kind:"date"). 별도 칩으로 노출.
  const [calendarOnly, setCalendarOnly] = useState(false);

  const hasActiveFilters = useMemo(
    () =>
      Boolean(text.trim()) ||
      kind !== "all" ||
      status !== null ||
      Boolean(from) ||
      Boolean(to) ||
      calendarOnly ||
      !includeTest,
    [text, kind, status, from, to, calendarOnly, includeTest],
  );

  const resetFilters = useCallback(() => {
    setText("");
    setKind("all");
    setStatus(null);
    setFrom("");
    setTo("");
    setCalendarOnly(false);
    setIncludeTest(true);
    setLimit(PAGE_SIZE);
  }, []);

  // 활성 필터 요약 라벨.
  const activeFilterLabels = useMemo(() => {
    const labels: string[] = [];
    if (text.trim()) labels.push(`검색 "${text.trim()}"`);
    if (calendarOnly) labels.push("캘린더");
    else if (kind !== "all") {
      labels.push(KIND_CHIPS.find((chip) => chip.value === kind)?.label ?? String(kind));
    }
    if (status === "sent") labels.push("발송 성공");
    if (status === "failed") labels.push("발송 실패");
    if (from) labels.push(`${from}~`);
    if (to) labels.push(`~${to}`);
    if (!includeTest) labels.push("테스트 제외");
    return labels;
  }, [text, kind, status, from, to, calendarOnly, includeTest]);

  const runSearch = useCallback(
    async (uid: string) => {
      setSearching(true);
      try {
        // 캘린더 칩이 켜지면 date 종류로 좁힌다(캘린더 기반 알림은 kind:"date").
        const effectiveKind: AlertKind | undefined =
          calendarOnly ? "date" : kind === "all" ? undefined : kind;

        let rows: NotificationLog[];
        if (!hasActiveFilters) {
          rows = await loadNotificationLogs(uid, { limit });
        } else {
          rows = await searchNotificationLogs(uid, {
            text: text.trim() || undefined,
            kind: effectiveKind,
            status: status ?? undefined,
            from: from || undefined,
            to: to || undefined,
            includeTest,
            limit,
          });
        }
        setLogs(rows);
      } catch {
        setLogs([]);
      } finally {
        setSearching(false);
        setLoading(false);
      }
    },
    [text, kind, status, from, to, limit, calendarOnly, includeTest, hasActiveFilters],
  );

  // 디바운스된 검색 트리거.
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!user) return;
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = setTimeout(() => {
      void runSearch(user.uid);
    }, 250);
    return () => {
      if (timerRef.current) clearTimeout(timerRef.current);
    };
  }, [user, runSearch]);

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-foreground">발송 기록</h1>
        {searching ? <Loader2 size={16} className="animate-spin text-muted-foreground" /> : <Badge>{logs.length}</Badge>}
      </div>

      {/* 검색 박스 */}
      <div className="relative">
        <Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground" />
        <input
          type="search"
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            setLimit(PAGE_SIZE);
          }}
          placeholder="규칙 이름 · 종목 검색 (예: BXSL)"
          className="h-10 w-full rounded-xl border border-border bg-card pl-9 pr-9 text-sm text-foreground outline-none placeholder:text-muted-foreground focus:border-accent"
        />
        {text ? (
          <button
            type="button"
            onClick={() => {
              setText("");
              setLimit(PAGE_SIZE);
            }}
            aria-label="검색어 지우기"
            className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded-md p-1 text-muted-foreground hover:text-foreground"
          >
            <X size={16} />
          </button>
        ) : null}
      </div>

      {/* 필터 칩 (종류 + 캘린더 + 발송 상태) */}
      <div className="flex flex-wrap gap-2">
        {KIND_CHIPS.map((chip) => (
          <Chip
            key={chip.value}
            active={!calendarOnly && kind === chip.value}
            onClick={() => {
              setCalendarOnly(false);
              setKind(chip.value);
              setLimit(PAGE_SIZE);
            }}
          >
            {chip.label}
          </Chip>
        ))}
        <Chip
          active={calendarOnly}
          onClick={() => {
            setCalendarOnly((prev) => !prev);
            setKind("all");
            setLimit(PAGE_SIZE);
          }}
        >
          캘린더
        </Chip>
        <Chip
          active={status === "sent"}
          onClick={() => {
            setStatus((prev) => (prev === "sent" ? null : "sent"));
            setLimit(PAGE_SIZE);
          }}
        >
          발송 성공
        </Chip>
        <Chip
          active={status === "failed"}
          onClick={() => {
            setStatus((prev) => (prev === "failed" ? null : "failed"));
            setLimit(PAGE_SIZE);
          }}
        >
          발송 실패
        </Chip>
      </div>

      {/* 기간 필터 */}
      <div className="flex items-center gap-2">
        <label className="flex flex-1 flex-col gap-1 text-[11px] text-muted-foreground">
          시작일
          <input
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              setLimit(PAGE_SIZE);
            }}
            className="h-9 rounded-xl border border-border bg-card px-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        <label className="flex flex-1 flex-col gap-1 text-[11px] text-muted-foreground">
          종료일
          <input
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              setLimit(PAGE_SIZE);
            }}
            className="h-9 rounded-xl border border-border bg-card px-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
      </div>

      {/* 테스트 포함 토글 */}
      <div className="flex items-center justify-between rounded-xl border border-border bg-card px-3 py-2">
        <span className="text-xs text-muted-foreground">테스트 발송 기록 포함</span>
        <Toggle
          checked={includeTest}
          onChange={(next) => {
            setIncludeTest(next);
            setLimit(PAGE_SIZE);
          }}
          label="테스트 포함"
        />
      </div>

      {/* 활성 필터 요약 + 초기화 */}
      {hasActiveFilters ? (
        <div className="flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] text-muted-foreground">필터:</span>
          {activeFilterLabels.map((label) => (
            <Badge key={label} tone="accent">
              {label}
            </Badge>
          ))}
          <button
            type="button"
            onClick={resetFilters}
            className="ml-1 inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5 text-[11px] font-medium text-muted-foreground hover:text-foreground"
          >
            <X size={12} />필터 초기화
          </button>
        </div>
      ) : null}

      <p className="text-[11px] text-muted-foreground">
        텍스트 검색은 클라이언트 측 보조 필터로 동작합니다 (REQ-024). 종류·기간·테스트 필터는 서버 색인을 사용합니다.
      </p>

      {/* 결과 수 */}
      {!loading ? (
        <p className="text-xs text-muted-foreground">
          {searching ? "검색 중…" : `${logs.length}건의 기록`}
        </p>
      ) : null}

      {loading ? (
        <HistorySkeleton />
      ) : logs.length === 0 ? (
        <EmptyState
          icon={<ListChecks size={28} />}
          title={hasActiveFilters ? "조건에 맞는 기록이 없어요" : "아직 발송 기록이 없어요"}
          description={hasActiveFilters ? "검색어나 필터를 바꿔보세요." : "알림이 발송되면 여기에 기록이 남습니다."}
        />
      ) : (
        <div className="space-y-2">
          {logs.map((log) => (
            <LogRow key={log.id} log={log} />
          ))}
          {logs.length >= limit ? (
            <Button
              variant="secondary"
              className="w-full"
              onClick={() => setLimit((prev) => prev + PAGE_SIZE)}
              disabled={searching}
            >
              {searching ? <Loader2 size={14} className="animate-spin" /> : null}
              더 보기
            </Button>
          ) : null}
        </div>
      )}
    </div>
  );
}
