export type EngineState = "healthy" | "delayed" | "stopped" | "unknown";
export type EngineHealth = {
  state: EngineState;
  message: string;
  lastSuccessfulRunAt?: string;
  lastEvaluationSuccessAt?: string;
  lastPushAcceptedAt?: string;
  lastTelegramAcceptedAt?: string;
  evaluationErrors?: number;
  deliveryErrors?: number;
  workflows?: Record<string, string>;
};

// 30 min cadence + 45 min scheduling grace; a 3 hour gap is an outage.
export function classifyEngineHealth(heartbeat: Record<string, unknown> | null, workflows: Record<string, string>, now = Date.now()): EngineHealth {
  if (Object.values(workflows).some((state) => state.startsWith("disabled"))) {
    return { state: "stopped", message: "알림 워크플로가 비활성화되어 있습니다. 관리자 복구가 필요합니다.", workflows };
  }
  if (!heartbeat || !Object.keys(workflows).length || Object.values(workflows).some((state) => state !== "active")) {
    return { state: "unknown", message: "엔진 상태를 확인할 수 없습니다.", workflows };
  }
  const last = String(heartbeat.lastSuccessfulRunAt ?? "");
  const age = now - Date.parse(last);
  if (!Number.isFinite(age) || age < -60_000) {
    return { state: "unknown", message: "검증된 정상 실행 기록이 없습니다.", workflows };
  }
  const state: EngineState = age > 180 * 60_000 ? "stopped" : age > 75 * 60_000 || Number(heartbeat.evaluationErrors) > 0 || Number(heartbeat.deliveryErrors) > 0 ? "delayed" : "healthy";
  return {
    state, message: { healthy: "알림 엔진 정상", delayed: "알림 엔진 실행 지연 또는 오류", stopped: "알림 엔진 중단", unknown: "확인 불가" }[state],
    lastSuccessfulRunAt: last,
    lastEvaluationSuccessAt: heartbeat.lastEvaluationSuccessAt as string | undefined,
    lastPushAcceptedAt: heartbeat.lastPushAcceptedAt as string | undefined,
    lastTelegramAcceptedAt: heartbeat.lastTelegramAcceptedAt as string | undefined,
    evaluationErrors: Number(heartbeat.evaluationErrors ?? 0), deliveryErrors: Number(heartbeat.deliveryErrors ?? 0), workflows,
  };
}
