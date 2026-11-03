"use client";
import { useEffect, useState } from "react";
import type { EngineHealth } from "@/lib/alerts/engine-health";

export default function EngineHealthStatus() {
  const [health, setHealth] = useState<EngineHealth>({ state: "unknown", message: "엔진 상태 확인 중…" });
  useEffect(() => {
    const controller = new AbortController();
    fetch("/api/engine-health", { signal: controller.signal, cache: "no-store" })
      .then((res) => { if (!res.ok) throw new Error(); return res.json(); })
      .then(setHealth)
      .catch(() => { if (!controller.signal.aborted) setHealth({ state: "unknown", message: "엔진 상태 확인 불가" }); });
    return () => controller.abort();
  }, []);
  const time = (value?: string) => value ? new Date(value).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" }) : "확인된 기록 없음";
  return <div role="status" className="rounded-xl border border-border bg-card p-3 text-xs">
    <p className={health.state === "healthy" ? "text-success" : "text-warning"}>{health.message}</p>
    <details className="mt-2 text-muted-foreground"><summary className="cursor-pointer">엔진 상세</summary>
      <p>마지막 정상 완료: {time(health.lastSuccessfulRunAt)}</p>
      <p>규칙 평가 성공: {time(health.lastEvaluationSuccessAt)}</p>
      <p>Push 공급자 접수: {time(health.lastPushAcceptedAt)}</p>
      <p>Telegram 공급자 접수: {time(health.lastTelegramAcceptedAt)}</p>
      <p>현재 실행의 평가 오류 {health.evaluationErrors ?? "미확인"}건 · 전달 오류 {health.deliveryErrors ?? "미확인"}건</p>
      <p>공급자 접수는 기기에서의 수신 확인과 다릅니다. 75분 지연 · 180분 중단 기준입니다.</p>
    </details>
  </div>;
}
