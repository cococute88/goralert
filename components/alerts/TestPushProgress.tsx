"use client";
import Link from "next/link";
import { CheckCircle2, Loader2, XCircle, Circle, ListChecks, ArrowRight } from "lucide-react";
import type { NotificationChannelResult } from "@/lib/alerts/types";
import { cx } from "@/components/alerts/ui";

type TestErrorStage = "request" | "dispatch" | "actions" | "delivery";

export type TestPhase =
  | { kind: "idle" }
  | { kind: "requested" } // 요청 접수
  | { kind: "dispatching" } // GitHub Actions 시작
  | { kind: "waiting" }
  | { kind: "running" } // 엔진 실행 중
  | { kind: "delivering" } // Push 발송
  | { kind: "done"; channels: string } // 완료
  | { kind: "partial"; channels: string }
  | { kind: "unknown" }
  | { kind: "timeout" } // 상태 확인 기한 초과; 실행 여부 추정 금지
  | { kind: "error"; stage: TestErrorStage; message: string; detail?: string; who?: string };

type StepStatus = "done" | "active" | "error" | "pending";

const TEST_STEPS = [
  "요청 접수",
  "워크플로 시작 대기",
  "엔진 실행 확인",
  "채널 발송",
  "완료",
] as const;

// Index of the step a failure at `stage` maps onto.
const ERROR_STEP_INDEX: Record<TestErrorStage, number> = {
  request: 0,
  dispatch: 1,
  actions: 2,
  delivery: 3,
};

export function computeStepStatuses(phase: TestPhase): StepStatus[] {
  const build = (activeIdx: number): StepStatus[] =>
    TEST_STEPS.map((_, i) => (i < activeIdx ? "done" : i === activeIdx ? "active" : "pending"));

  switch (phase.kind) {
    case "requested":
      return build(0);
    case "dispatching":
    case "waiting":
    case "timeout":
      return build(1);
    case "running":
      return build(2);
    case "delivering":
      return build(3);
    case "done":
      return TEST_STEPS.map(() => "done");
    case "partial":
      return ["done", "done", "done", "error", "pending"];
    case "unknown":
      return ["done", "done", "done", "error", "pending"];
    case "error": {
      const errIdx = ERROR_STEP_INDEX[phase.stage];
      return TEST_STEPS.map((_, i) => (i < errIdx ? "done" : i === errIdx ? "error" : "pending"));
    }
    default:
      return TEST_STEPS.map(() => "pending");
  }
}

function StepIcon({ status }: { status: StepStatus }) {
  if (status === "done") return <CheckCircle2 size={16} className="text-success" />;
  if (status === "active") return <Loader2 size={16} className="animate-spin text-accent" />;
  if (status === "error") return <XCircle size={16} className="text-danger" />;
  return <Circle size={16} className="text-muted-foreground/40" />;
}

function errorText(phase: Extract<TestPhase, { kind: "error" }>): string {
  const prefix =
    phase.stage === "request"
      ? "요청 접수 실패"
      : phase.stage === "dispatch"
        ? "Dispatch 실패 — GitHub Actions를 시작하지 못했습니다"
        : phase.stage === "actions"
          ? "Actions 실패 — 엔진 실행 중 오류가 발생했습니다"
          : `채널 발송 실패 — ${phase.who ?? "Push"} 전송에 실패했습니다`;
  return phase.message ? `${prefix}\n${phase.message}` : prefix;
}

export function TestPushProgress({ phase }: { phase: TestPhase }) {
  if (phase.kind === "idle") return null;
  const statuses = computeStepStatuses(phase);
  const terminal = phase.kind === "done" || phase.kind === "partial" || phase.kind === "unknown" || phase.kind === "timeout" || phase.kind === "error";

  let message: string | null = null;
  let messageTone = "text-muted-foreground";
  if (phase.kind === "requested") message = "요청을 접수했어요…";
  else if (phase.kind === "dispatching") message = "GitHub Actions를 시작하는 중…";
  else if (phase.kind === "running") message = "엔진이 실행 중입니다…";
  else if (phase.kind === "waiting") message = "실행 요청이 접수되었습니다. 엔진의 처리 시작 확인을 기다립니다.";
  else if (phase.kind === "delivering") message = "채널 발송을 시작한 기록이 확인되었습니다…";
  else if (phase.kind === "partial") { message = `일부 성공 (${phase.channels}) · 나머지 채널은 기록에서 확인하세요`; messageTone = "text-warning"; }
  else if (phase.kind === "unknown") { message = "발송 결과 불명: 중복 방지를 위해 자동 재발송하지 않습니다. 채널별 기록을 확인하세요."; messageTone = "text-warning"; }
  else if (phase.kind === "done") {
    message = `발송 완료${phase.channels ? ` (${phase.channels})` : ""} · 기록 탭에서 확인하세요`;
    messageTone = "text-success";
  } else if (phase.kind === "timeout") {
    // 요구사항 2: 90초 이상 걸릴 때의 안내.
    message = "상태 확인 시간 초과: 실행 여부와 발송 결과를 확인하지 못했습니다.\n요청은 기록에 남아 있습니다. 중복 요청 전에 결과를 확인하세요.";
    messageTone = "text-warning";
  } else if (phase.kind === "error") {
    message = errorText(phase);
    messageTone = "text-danger";
  }

  return (
    <div
      className="mt-3 rounded-xl border border-border bg-background/60 p-3"
      role="status"
      aria-live="polite"
    >
      <ol>
        {TEST_STEPS.map((label, i) => {
          const status = statuses[i];
          const isLast = i === TEST_STEPS.length - 1;
          return (
            <li key={label} className="flex gap-2.5">
              <div className="flex flex-col items-center">
                <StepIcon status={status} />
                {!isLast ? (
                  <span
                    className={cx("my-0.5 w-px flex-1", status === "done" ? "bg-success/50" : "bg-border")}
                    style={{ minHeight: 12 }}
                  />
                ) : null}
              </div>
              <span
                className={cx(
                  "pb-3 text-xs leading-4",
                  status === "done"
                    ? "text-foreground"
                    : status === "active"
                      ? "font-medium text-accent"
                      : status === "error"
                        ? "font-medium text-danger"
                        : "text-muted-foreground",
                )}
              >
                {label}
              </span>
            </li>
          );
        })}
      </ol>

      {message ? (
        <div className="mt-1 border-t border-border pt-2.5">
          <p className={cx("whitespace-pre-line text-xs", messageTone)}>{message}</p>
          {phase.kind === "error" && phase.detail ? <details className="mt-2 text-xs text-muted-foreground"><summary>기술 상세</summary><p>{phase.detail}</p></details> : null}
          {terminal ? (
            <Link
              href="/history"
              className="mt-2.5 inline-flex h-8 items-center gap-1.5 rounded-xl border border-border bg-card px-3 text-xs font-medium text-foreground transition-colors hover:bg-muted"
            >
              <ListChecks size={14} />
              기록으로 이동
              <ArrowRight size={14} />
            </Link>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function TestChannelResults({ results }: { results: NotificationChannelResult[] }) {
  if (!results.length) return null;
  return (
    <ul className="mt-3 space-y-1 rounded-xl border border-border bg-background/60 p-3 text-xs" aria-live="polite">
      {results.map((result) => (
        <li key={result.channel} className="flex items-start justify-between gap-3">
          <span>{result.channel === "telegram" ? "Telegram" : "Push 알림"}</span>
          <span className={result.status === "sent" ? "text-success" : "text-danger"}>
            {result.status === "sent" ? "공급자 접수 성공" : result.status === "pending" ? "보류 / 대기" : result.status === "sending" ? "발송 시작 확인" : result.status === "unknown" ? "결과 불명 · 자동 재발송 안 함" : `실패${result.error ? ` — ${result.error}` : ""}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

