"use client";

// GORALERT-ALERT-SYSTEM Layer B2 (REQ-045 / REQ-039)
// 설정 탭.
//   - Google 로그인: 현재 사용자 표시 + 로그아웃
//   - Telegram 설정: telegramChatId
//   - Push 설정: 이 기기 알림 등록(실제 FCM) + 등록된 기기 수 + 권한 상태
//   - 기본 알림 시간 / 기본 문구 / 전체 사용 토글
//   - 채널 테스트(REQ-039): testPushRequests 큐에 요청을 넣으면 Python 엔진이
//     운영과 동일한 경로(send_test_alert → PushChannel)로 발송하고 isTest 로그를 남김

import { useEffect, useState } from "react";
import Link from "next/link";
import {
  LogOut,
  Loader2,
  Save,
  Send,
  Smartphone,
  BellOff,
  CheckCircle2,
  XCircle,
  Circle,
  ArrowRight,
  ListChecks,
  ChevronDown,
  ChevronUp,
  RotateCcw,
  Trash2,
} from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type {
  AlertSettings,
  DeliveryChannel,
  MessageTemplate,
  PushDevice,
  PushDeviceBrowser,
  PushDevicePlatform,
} from "@/lib/alerts/types";
import {
  enqueueTestPushRequest,
  loadAlertSettings,
  removePushRegistrations,
  resetPushRegistrations,
  saveAlertSettings,
  waitForTestPushResult,
  type PushRegistrationTarget,
} from "@/lib/alerts/repositories";
import {
  deleteCurrentPushTokenLocally,
  inspectCurrentPushRegistration,
  isPushSupported,
  getPushDiagnostics,
  registerPushToken,
  type CurrentPushRegistration,
  type PushDiagnostics,
} from "@/lib/alerts/fcm-client";
import { dispatchTestPushWorkflow } from "@/lib/alerts/test-push";
import { Badge, Button, Card, CardSection, ConfirmDialog, Toggle, cx } from "@/components/alerts/ui";
import { useToast } from "@/components/alerts/ui/toast";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";

// --- Test-push progress UX (Release UX Polish Sprint) ------------------------
// Purely presentational state machine over the EXISTING test-push flow
// (enqueue -> workflow_dispatch -> engine drains via PushChannel/FCM). It does
// NOT change any delivery logic — it only surfaces where a run currently is so
// the user can understand the async pipeline:
//   요청 접수 → GitHub Actions 시작 → 엔진 실행 중 → Push 발송 → 완료
// and distinguishes the three failure points (Dispatch / Actions / FCM) plus the
// 90s "still running, check 기록" guidance.

type TestErrorStage = "request" | "dispatch" | "actions" | "delivery";

type TestPhase =
  | { kind: "idle" }
  | { kind: "requested" } // 요청 접수
  | { kind: "dispatching" } // GitHub Actions 시작
  | { kind: "running" } // 엔진 실행 중
  | { kind: "delivering" } // Push 발송
  | { kind: "done"; channels: string } // 완료
  | { kind: "timeout" } // 90s 초과 — 엔진은 계속 실행 중
  | { kind: "error"; stage: TestErrorStage; message: string; who?: string };

type StepStatus = "done" | "active" | "error" | "pending";

const TEST_STEPS = [
  "요청 접수",
  "GitHub Actions 시작",
  "엔진 실행 중",
  "Push 발송",
  "완료",
] as const;

// Index of the step a failure at `stage` maps onto.
const ERROR_STEP_INDEX: Record<TestErrorStage, number> = {
  request: 0,
  dispatch: 1,
  actions: 2,
  delivery: 3,
};

function computeStepStatuses(phase: TestPhase): StepStatus[] {
  const build = (activeIdx: number): StepStatus[] =>
    TEST_STEPS.map((_, i) => (i < activeIdx ? "done" : i === activeIdx ? "active" : "pending"));

  switch (phase.kind) {
    case "requested":
      return build(0);
    case "dispatching":
      return build(1);
    case "running":
    case "timeout":
      return build(2);
    case "delivering":
      return build(3);
    case "done":
      return TEST_STEPS.map(() => "done");
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

function TestPushProgress({ phase }: { phase: TestPhase }) {
  if (phase.kind === "idle") return null;
  const statuses = computeStepStatuses(phase);
  const terminal = phase.kind === "done" || phase.kind === "timeout" || phase.kind === "error";

  let message: string | null = null;
  let messageTone = "text-muted-foreground";
  if (phase.kind === "requested") message = "요청을 접수했어요…";
  else if (phase.kind === "dispatching") message = "GitHub Actions를 시작하는 중…";
  else if (phase.kind === "running") message = "엔진이 실행 중입니다…";
  else if (phase.kind === "delivering") message = "Push를 발송하는 중…";
  else if (phase.kind === "done") {
    message = `발송 완료${phase.channels ? ` (${phase.channels})` : ""} · 기록 탭에서 확인하세요`;
    messageTone = "text-success";
  } else if (phase.kind === "timeout") {
    // 요구사항 2: 90초 이상 걸릴 때의 안내.
    message = "엔진이 실행 중입니다.\n기록 탭에서 결과를 확인할 수 있습니다.";
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

function TestChannelResults({ results }: { results: Array<{ channel: DeliveryChannel; status: "sent" | "failed"; error?: string }> }) {
  if (!results.length) return null;
  return (
    <ul className="mt-3 space-y-1 rounded-xl border border-border bg-background/60 p-3 text-xs" aria-live="polite">
      {results.map((result) => (
        <li key={result.channel} className="flex items-start justify-between gap-3">
          <span>{result.channel === "telegram" ? "Telegram" : "Push 알림"}</span>
          <span className={result.status === "sent" ? "text-success" : "text-danger"}>
            {result.status === "sent" ? "성공" : `실패${result.error ? ` — ${result.error}` : ""}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

type PushDeviceListItem = {
  key: string;
  deviceId?: string;
  token: string;
  label: string;
  detail: string;
  timeLabel: string;
  current: boolean;
};

const PUSH_PLATFORM_LABELS: Record<PushDevicePlatform, string> = {
  android: "Android",
  windows: "Windows",
  macos: "macOS",
  ios: "iOS",
  linux: "Linux",
  unknown: "알 수 없는 플랫폼",
};

const PUSH_BROWSER_LABELS: Record<PushDeviceBrowser, string> = {
  chrome: "Chrome",
  "samsung-internet": "삼성 인터넷",
  edge: "Edge",
  safari: "Safari",
  firefox: "Firefox",
  unknown: "알 수 없는 브라우저",
};

function formatPushDate(value?: string): string {
  if (!value || Number.isNaN(Date.parse(value))) return "확인 시각 없음";
  return new Intl.DateTimeFormat("ko-KR", {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

function legacyTokenKey(token: string): string {
  // Stable UI-only fingerprint. The full token is never rendered or used as a
  // React key; actual deletion still carries the exact selected token in memory.
  let hash = 0x811c9dc5;
  for (let index = 0; index < token.length; index += 1) {
    hash ^= token.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return `legacy:${token.length}:${(hash >>> 0).toString(36)}`;
}

function buildPushDeviceList(
  settings: AlertSettings,
  current: CurrentPushRegistration,
): PushDeviceListItem[] {
  const devices = (settings.pushDevices ?? []).filter(
    (device): device is PushDevice => Boolean(device?.id && device?.token),
  );
  const linkedTokens = new Set(devices.map((device) => device.token));
  const metadataItems = devices.map((device) => ({
    key: `device:${device.id}`,
    deviceId: device.id,
    token: device.token,
    label: device.label || "알 수 없는 기기",
    detail: `${PUSH_PLATFORM_LABELS[device.platform]} · ${PUSH_BROWSER_LABELS[device.browser]}`,
    timeLabel: `최근 확인 ${formatPushDate(device.lastSeenAt || device.registeredAt)}`,
    current: device.id === current.deviceId || device.token === current.token,
  }));
  const legacyItems = Array.from(new Set(settings.pushTokens ?? []))
    .filter((token) => token && !linkedTokens.has(token))
    .map((token) => ({
      key: legacyTokenKey(token),
      token,
      label: "기존 등록 기기",
      detail: "기기 정보 없음",
      timeLabel: "등록 시각 정보 없음",
      current: token === current.token,
    }));
  return [...metadataItems, ...legacyItems];
}

function SectionCard({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardSection className="space-y-3">
        <div>
          <h2 className="text-sm font-semibold text-foreground">{title}</h2>
          {description ? <p className="mt-0.5 text-xs text-muted-foreground">{description}</p> : null}
        </div>
        {children}
      </CardSection>
    </Card>
  );
}

const FIELD_CLASS =
  "h-10 w-full rounded-xl border border-border bg-background px-3 text-sm text-foreground outline-none focus:border-accent";

export default function GoralertSettingsPage() {
  const toast = useToast();
  const { user, loading: authLoading, logout } = useFirebaseAuth();

  const [settings, setSettings] = useState<AlertSettings>({ globalEnabled: true });
  const [loading, setLoading] = useState(true);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [phase, setPhase] = useState<TestPhase>({ kind: "idle" });
  const [testResults, setTestResults] = useState<Array<{ channel: DeliveryChannel; status: "sent" | "failed"; error?: string }>>([]);

  // Push 등록 상태.
  const [registering, setRegistering] = useState(false);
  const [removingDevices, setRemovingDevices] = useState(false);
  const [resettingDevices, setResettingDevices] = useState(false);
  const [deviceManagerOpen, setDeviceManagerOpen] = useState(false);
  const [removeConfirmOpen, setRemoveConfirmOpen] = useState(false);
  const [resetConfirmOpen, setResetConfirmOpen] = useState(false);
  const [selectedDevices, setSelectedDevices] = useState<Map<string, PushDeviceListItem>>(new Map());
  const [currentPush, setCurrentPush] = useState<CurrentPushRegistration>({});
  const [checkingCurrentPush, setCheckingCurrentPush] = useState(true);
  const [pushSupported, setPushSupported] = useState<boolean | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");
  const [pushDiagnostics, setPushDiagnostics] = useState<PushDiagnostics | null>(null);
  const showPushDiagnostics = process.env.NODE_ENV !== "production";

  // 입력 버퍼(텍스트 필드는 blur 시 저장).
  const [telegramChatId, setTelegramChatId] = useState("");
  const [defaultAlertTime, setDefaultAlertTime] = useState("");
  const [defaultMessageTitle, setDefaultMessageTitle] = useState("");
  const [defaultMessageBody, setDefaultMessageBody] = useState("");

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);
    loadAlertSettings(user.uid)
      .then(async (loaded) => {
        if (!active) return;
        setSettings(loaded);
        setTelegramChatId(loaded.telegramChatId ?? "");
        setDefaultAlertTime(loaded.defaultAlertTime ?? "");
        setDefaultMessageTitle(loaded.defaultMessageTitle ?? "");
        setDefaultMessageBody(loaded.defaultMessageBody ?? "");
        const current = await inspectCurrentPushRegistration(user.uid);
        if (!active) return;
        setCurrentPush(current);
        if (current.error) toast.error(current.error);
        if (current.snapshot?.changed) {
          setSettings((previous) => ({ ...previous, ...current.snapshot }));
        }
      })
      .catch(() => {
        if (active) {
          setSettings({ globalEnabled: true });
          toast.error("기기 목록 불러오기에 실패했습니다.");
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
          setCheckingCurrentPush(false);
        }
      });
    return () => {
      active = false;
    };
  }, [toast, user]);

  // 푸시 지원 여부 + 현재 권한 상태 감지(클라이언트 전용).
  useEffect(() => {
    let active = true;
    if (typeof Notification !== "undefined") {
      setPermission(Notification.permission);
    } else {
      setPermission("unsupported");
    }
    isPushSupported()
      .then((supported) => {
        if (active) setPushSupported(supported);
      })
      .catch(() => {
        if (active) setPushSupported(false);
      });
    return () => {
      active = false;
    };
  }, []);

  const persist = async (field: string, partial: Partial<AlertSettings>) => {
    if (!user) return;
    setSavingField(field);
    try {
      await saveAlertSettings(user.uid, partial);
      setSettings((prev) => ({ ...prev, ...partial }));
      toast.success("저장했어요");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "저장에 실패했습니다");
    } finally {
      setSavingField(null);
    }
  };

  const handleRegisterToken = async () => {
    if (!user) return;
    setRegistering(true);
    try {
      const result = await registerPushToken(user.uid);
      if (result.ok && result.snapshot) {
        setSettings((prev) => ({ ...prev, ...result.snapshot }));
        setCurrentPush((prev) => ({ ...prev, deviceId: result.deviceId }));
        setPermission(typeof Notification !== "undefined" ? Notification.permission : "granted");
        toast.success("이 기기를 알림 대상으로 등록했어요");
      } else {
        if (typeof Notification !== "undefined") setPermission(Notification.permission);
        toast.error(result.error ?? "알림 등록에 실패했습니다");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "알림 등록에 실패했습니다");
    } finally {
      setRegistering(false);
    }
  };

  const refreshPushRegistrations = async () => {
    if (!user) return;
    setCheckingCurrentPush(true);
    try {
      const [loaded, current] = await Promise.all([
        loadAlertSettings(user.uid),
        inspectCurrentPushRegistration(user.uid),
      ]);
      setCurrentPush(current);
      setSettings({ ...loaded, ...(current.snapshot?.changed ? current.snapshot : {}) });
      if (current.error) toast.error(current.error);
    } catch {
      toast.error("기기 목록 불러오기에 실패했습니다.");
    } finally {
      setCheckingCurrentPush(false);
    }
  };

  const handleRemoveSelectedDevices = async () => {
    if (!user || removingDevices) return;
    const selectedItems = Array.from(selectedDevices.values());
    if (!selectedItems.length) return;
    setRemovingDevices(true);
    try {
      const targets: PushRegistrationTarget[] = selectedItems.map((item) => ({
        deviceId: item.deviceId,
        token: item.token,
      }));
      const removingCurrent = selectedItems.some((item) => item.current);
      const snapshot = await removePushRegistrations(user.uid, targets);
      setSettings((prev) => ({ ...prev, ...snapshot }));
      setSelectedDevices(new Map());
      setRemoveConfirmOpen(false);
      if (removingCurrent) {
        const local = await deleteCurrentPushTokenLocally(user.uid);
        setCurrentPush({});
        if (!local.ok) {
          toast.error("서버 등록은 해제했지만 현재 기기의 로컬 알림 정보 삭제에 실패했습니다.");
          return;
        }
      }
      toast.success(`${selectedItems.length}개 기기의 알림 등록을 해제했습니다.`);
    } catch {
      toast.error("선택한 기기 알림 등록 해제에 실패했습니다. 다시 시도해 주세요.");
    } finally {
      setRemovingDevices(false);
    }
  };

  const handleResetPushRegistrations = async () => {
    if (!user || resettingDevices) return;
    setResettingDevices(true);
    try {
      const snapshot = await resetPushRegistrations(user.uid);
      setSettings((prev) => ({ ...prev, ...snapshot }));
      setSelectedDevices(new Map());
      setResetConfirmOpen(false);
      const local = await deleteCurrentPushTokenLocally(user.uid);
      setCurrentPush({});
      if (!local.ok) {
        toast.error("서버의 기기 등록은 초기화했지만 현재 기기의 로컬 알림 정보 삭제에 실패했습니다.");
        return;
      }
      toast.success("기기 알림 등록을 초기화했습니다. 알림을 받을 기기에서 다시 등록해 주세요.");
    } catch {
      toast.error("기기 알림 등록 초기화에 실패했습니다. 서버 등록 정보는 변경되지 않았습니다.");
    } finally {
      setResettingDevices(false);
    }
  };

  const refreshPushDiagnostics = async () => {
    if (!user) return;
    setPushDiagnostics(await getPushDiagnostics(user.uid));
  };

  // Test sends go through the SAME production path as scheduled alerts:
  //   1) ENQUEUE users/{uid}/testPushRequests (no in-browser delivery)
  //   2) fire the alert-test-push workflow immediately (thin /api/test-push bridge)
  //   3) the Python engine drains it via send_test_alert -> deliver -> PushChannel
  //      and writes the isTest NotificationLog + the request result
  //   4) observe the request doc for the engine's REAL channel result (no fake "sent")
  const handleTest = async (key: string, channels: DeliveryChannel[]) => {
    // 요구사항 4/5: 이미 테스트가 진행 중이면 중복 클릭을 무시한다.
    if (!user || testing) return;
    setTesting(key);
    setPhase({ kind: "requested" }); // 요청 접수
    setTestResults([]);
    const label = (c: DeliveryChannel) => (c === "telegram" ? "Telegram" : "Push");
    try {
      const message: MessageTemplate = {
        title: settings.defaultMessageTitle?.trim() || "고라알림 테스트",
        body: settings.defaultMessageBody?.trim() || "설정 화면에서 보낸 테스트 알림입니다",
      };
      const requestId = await enqueueTestPushRequest(user.uid, { channels, message });

      // Trigger the engine immediately instead of waiting for the cron.
      setPhase({ kind: "dispatching" }); // GitHub Actions 시작
      const dispatch = await dispatchTestPushWorkflow(user);
      if (!dispatch.ok) {
        // 요구사항 3: Dispatch 실패 (workflow_dispatch 트리거 자체 실패).
        // 문서는 이미 큐에 있어 5분 주기 cron이 처리하지만, 즉시 트리거 실패를 알린다.
        setPhase({
          kind: "error",
          stage: "dispatch",
          message: `${dispatch.error ?? "워크플로우를 시작하지 못했어요"} · 5분 간격 fallback 처리 설정에 따라 기록 탭에서 결과를 확인하세요`,
        });
        toast.error(`Dispatch 실패 — ${dispatch.error ?? "워크플로우를 시작하지 못했어요"}`);
        return;
      }

      // 엔진 실행 중 — 엔진이 결과 문서를 확정할 때까지 관찰(최대 90초).
      setPhase({ kind: "running" });
      toast.show("테스트 발송을 시작했어요 · 결과를 기다리는 중…", "info");
      const result = await waitForTestPushResult(user.uid, requestId);

      if (!result) {
        // 요구사항 2: 90초 초과 — 실패가 아니라 "아직 실행 중" 안내.
        setPhase({ kind: "timeout" });
        toast.show("엔진이 실행 중입니다 · 기록 탭에서 결과를 확인하세요", "info");
        return;
      }

      const results = result.results ?? [];
      setTestResults(results);
      const failed = results.filter((c) => c.status === "failed");
      const sent = results.filter((c) => c.status === "sent");

      if (result.status === "error") {
        // 요구사항 3: Actions/엔진 실행 자체가 크래시한 경우.
        setPhase({
          kind: "error",
          stage: "actions",
          message: result.error ?? "엔진 실행 중 오류가 발생했습니다",
        });
        toast.error(`Actions 실패${result.error ? ` — ${result.error}` : ""} · 기록 탭에서 확인하세요`);
        return;
      }

      if (result.status !== "done" || failed.length > 0) {
        // 요구사항 3: 채널(FCM/Telegram) 발송 실패.
        const detail = failed[0]?.error ? ` — ${failed[0].error}` : result.error ? ` — ${result.error}` : "";
        const who = failed.map((c) => label(c.channel)).join(", ") || channels.map(label).join(", ");
        setPhase({
          kind: "error",
          stage: "delivery",
          who,
          message: failed[0]?.error ?? result.error ?? "발송에 실패했습니다",
        });
        toast.error(`${who} 발송 실패${detail} · 기록 탭에서 확인하세요`);
        return;
      }

      // 성공 — Push 발송 → 완료 단계를 짧게 노출한 뒤 완료 처리.
      const who = sent.map((c) => label(c.channel)).join(", ") || "채널 없음";
      setPhase({ kind: "delivering" });
      await new Promise((resolve) => setTimeout(resolve, 600));
      setPhase({ kind: "done", channels: who });
      toast.success(`테스트 발송 완료 (${who}) · 기록 탭에서 확인하세요`);
    } catch (err) {
      setPhase({
        kind: "error",
        stage: "request",
        message: err instanceof Error ? err.message : "테스트 발송에 실패했습니다",
      });
      toast.error(err instanceof Error ? err.message : "테스트 발송에 실패했습니다");
    } finally {
      setTesting(null);
    }
  };

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;

  const pushDeviceItems = buildPushDeviceList(settings, currentPush);
  const pushCount = pushDeviceItems.length;
  const currentDeviceRegistered = pushDeviceItems.some((item) => item.current);
  const permissionLabel =
    permission === "granted"
      ? "허용됨"
      : permission === "denied"
        ? "차단됨"
        : permission === "unsupported"
          ? "미지원"
          : "미설정";
  const permissionTone: "success" | "danger" | "neutral" =
    permission === "granted" ? "success" : permission === "denied" ? "danger" : "neutral";

  return (
    <div className="space-y-4">
      <h1 className="text-lg font-bold text-foreground">설정</h1>

      {/* Google 로그인 */}
      <SectionCard title="Google 로그인" description="고라알림은 Gorani 계정으로 동작합니다.">
        <div className="flex items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">
              {user.displayName ?? "이름 없음"}
            </p>
            <p className="truncate text-xs text-muted-foreground">{user.email ?? user.uid}</p>
          </div>
          <Button variant="secondary" size="sm" onClick={() => void logout()}>
            <LogOut size={14} />로그아웃
          </Button>
        </div>
      </SectionCard>

      {/* 전체 사용 토글 */}
      <SectionCard title="전체 알림" description="끄면 모든 알림 발송이 중단됩니다.">
        <div className="flex items-center justify-between">
          <span className="text-sm text-foreground">전체 알림 사용</span>
          <Toggle
            checked={settings.globalEnabled}
            onChange={(next) => void persist("globalEnabled", { globalEnabled: next })}
            label="전체 알림 사용"
          />
        </div>
      </SectionCard>

      {/* Telegram 설정 */}
      <SectionCard title="Telegram 설정" description="알림을 받을 Telegram Chat ID 를 입력하세요.">
        <input
          type="text"
          inputMode="numeric"
          value={telegramChatId}
          onChange={(e) => setTelegramChatId(e.target.value)}
          onBlur={() => {
            const next = telegramChatId.trim();
            if (next !== (settings.telegramChatId ?? "")) {
              void persist("telegramChatId", { telegramChatId: next });
            }
          }}
          placeholder="예: 123456789"
          className={FIELD_CLASS}
        />
        {savingField === "telegramChatId" ? (
          <p className="text-[11px] text-muted-foreground">저장 중…</p>
        ) : null}
      </SectionCard>

      {/* Push 설정 */}
      <SectionCard
        title="Push 설정"
        description="이 기기를 알림 대상으로 등록합니다. Android/데스크톱 브라우저를 지원하며 iOS Safari 등 일부 환경은 지원되지 않습니다."
      >
        <div className="flex items-center justify-between">
          <span className="text-sm text-foreground">등록된 기기</span>
          <Badge tone={pushCount > 0 ? "success" : "neutral"}>{pushCount}대</Badge>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-foreground">현재 기기</span>
          <Badge tone={currentDeviceRegistered ? "success" : "neutral"}>
            {checkingCurrentPush ? "확인 중" : currentDeviceRegistered ? "등록됨" : "미등록"}
          </Badge>
        </div>
        <div className="flex items-center justify-between">
          <span className="text-sm text-foreground">알림 권한</span>
          <Badge tone={permissionTone}>{permissionLabel}</Badge>
        </div>
        <Button
          variant="secondary"
          size="sm"
          className="min-h-11 w-full"
          onClick={() => void handleRegisterToken()}
          disabled={registering || removingDevices || resettingDevices || pushSupported === false || permission === "denied"}
        >
          {registering ? <Loader2 size={14} className="animate-spin" /> : <Smartphone size={14} />}
          이 기기 알림 등록
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="min-h-11 w-full"
          onClick={() => setDeviceManagerOpen((open) => !open)}
          disabled={registering || removingDevices || resettingDevices}
          aria-expanded={deviceManagerOpen}
          aria-controls="push-device-manager"
        >
          <BellOff size={14} />
          등록된 기기 알림 해제
          {deviceManagerOpen ? <ChevronUp size={14} /> : <ChevronDown size={14} />}
        </Button>

        {deviceManagerOpen ? (
          <div id="push-device-manager" className="space-y-3 rounded-xl border border-border bg-background/60 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="text-xs font-medium text-foreground">등록 기기 관리</p>
              <Button
                variant="ghost"
                size="sm"
                onClick={() => void refreshPushRegistrations()}
                disabled={checkingCurrentPush || removingDevices || resettingDevices}
              >
                {checkingCurrentPush ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
                등록 상태 확인
              </Button>
            </div>
            {pushDeviceItems.length ? (
              <ul className="space-y-2">
                {pushDeviceItems.map((item) => {
                  const checked = selectedDevices.has(item.key);
                  return (
                    <li key={item.key} className="rounded-xl border border-border bg-card p-3">
                      <label className="flex min-h-11 cursor-pointer items-start gap-3">
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={(event) => {
                            setSelectedDevices((previous) => {
                              const next = new Map(previous);
                              if (event.target.checked) next.set(item.key, item);
                              else next.delete(item.key);
                              return next;
                            });
                          }}
                          className="mt-1 h-5 w-5 shrink-0 accent-accent"
                          aria-label={`${item.label} 알림 등록 해제 선택`}
                        />
                        <span className="min-w-0 flex-1">
                          <span className="flex flex-wrap items-center gap-1.5">
                            <span className="break-words text-sm font-medium text-foreground">{item.label}</span>
                            {item.current ? <Badge tone="success">현재 기기</Badge> : null}
                          </span>
                          <span className="mt-0.5 block text-xs text-muted-foreground">{item.detail}</span>
                          <span className="mt-0.5 block text-[11px] text-muted-foreground">{item.timeLabel}</span>
                        </span>
                      </label>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="rounded-xl border border-dashed border-border px-3 py-6 text-center text-sm text-muted-foreground">
                등록된 알림 기기가 없습니다.
              </p>
            )}
            <Button
              variant="danger"
              size="sm"
              className="min-h-11 w-full"
              onClick={() => setRemoveConfirmOpen(true)}
              disabled={!selectedDevices.size || removingDevices || resettingDevices}
            >
              {removingDevices ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
              선택한 기기 알림 등록 해제
            </Button>
          </div>
        ) : null}

        <div className="border-t border-border pt-3">
          <Button
            variant="danger"
            size="sm"
            className="min-h-11 w-full"
            onClick={() => setResetConfirmOpen(true)}
            disabled={registering || removingDevices || resettingDevices}
          >
            {resettingDevices ? <Loader2 size={14} className="animate-spin" /> : <RotateCcw size={14} />}
            기기 알림 등록 초기화
          </Button>
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            모든 기기의 Push 알림 등록을 삭제한 뒤 필요한 기기에서 다시 등록할 수 있습니다.
          </p>
        </div>
        {pushSupported === false ? (
          <p className="text-[11px] text-muted-foreground">
            이 브라우저에서는 푸시 알림을 사용할 수 없습니다.
          </p>
        ) : permission === "denied" ? (
          <p className="text-[11px] text-danger">
            알림 권한이 차단되어 있습니다. 브라우저 설정에서 알림을 허용한 뒤 다시 시도하세요.
          </p>
        ) : null}
        {showPushDiagnostics ? (
          <div className="rounded-xl border border-dashed border-border p-3 text-[11px] text-muted-foreground">
            <div className="flex items-center justify-between gap-2">
              <span className="font-medium text-foreground">개발용 Push 진단</span>
              <Button variant="ghost" size="sm" onClick={() => void refreshPushDiagnostics()} disabled={registering || removingDevices || resettingDevices}>
                진단 새로고침
              </Button>
            </div>
            {pushDiagnostics ? (
              <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1">
                <dt>Notification API</dt><dd>{pushDiagnostics.notificationApi ? "지원" : "미지원"}</dd>
                <dt>Service Worker</dt><dd>{pushDiagnostics.serviceWorkerApi ? "지원" : "미지원"}</dd>
                <dt>Messaging 지원</dt><dd>{String(pushDiagnostics.messagingSupported)}</dd>
                <dt>VAPID key</dt><dd>{pushDiagnostics.vapidKeyConfigured ? "설정됨" : "누락"}</dd>
                <dt>SW 파일</dt><dd>{String(pushDiagnostics.workerScriptReachable)}</dd>
                <dt>SW 상태</dt><dd>{pushDiagnostics.workerState.active ? "active" : pushDiagnostics.workerState.waiting ? "waiting" : pushDiagnostics.workerState.installing ? "installing" : "없음"}</dd>
                <dt>Push 구독</dt><dd>{String(pushDiagnostics.pushSubscription)}</dd>
                <dt>Foreground handler</dt><dd>{pushDiagnostics.foregroundHandlerRegistered ? "등록됨" : "미등록"}</dd>
                <dt>현재 FCM token</dt><dd>{pushDiagnostics.currentToken?.issued ? `${pushDiagnostics.currentToken.masked} (${pushDiagnostics.currentToken.stored ? "Firestore 저장됨" : "Firestore 미저장"})` : pushDiagnostics.currentToken?.error ?? "확인 안 함"}</dd>
                <dt>SW scope</dt><dd className="truncate">{pushDiagnostics.workerScope ?? "없음"}</dd>
              </dl>
            ) : <p className="mt-2">진단은 개발 환경에서만 표시됩니다. 토큰 전체와 구독 endpoint는 표시하지 않습니다.</p>}
          </div>
        ) : null}
      </SectionCard>

      {/* 기본값 */}
      <SectionCard title="기본 알림 설정" description="새 알림을 만들 때 사용할 기본 시간과 문구입니다.">
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          기본 알림 시간
          <input
            type="time"
            value={defaultAlertTime}
            onChange={(e) => setDefaultAlertTime(e.target.value)}
            onBlur={() => {
              if (defaultAlertTime !== (settings.defaultAlertTime ?? "")) {
                void persist("defaultAlertTime", { defaultAlertTime: defaultAlertTime || undefined });
              }
            }}
            className={FIELD_CLASS}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          기본 문구 제목
          <input
            type="text"
            value={defaultMessageTitle}
            onChange={(e) => setDefaultMessageTitle(e.target.value)}
            onBlur={() => {
              if (defaultMessageTitle !== (settings.defaultMessageTitle ?? "")) {
                void persist("defaultMessageTitle", { defaultMessageTitle: defaultMessageTitle.trim() || undefined });
              }
            }}
            placeholder="예: 고라알림"
            className={FIELD_CLASS}
          />
        </label>
        <label className="flex flex-col gap-1 text-xs text-muted-foreground">
          기본 문구 본문
          <textarea
            value={defaultMessageBody}
            onChange={(e) => setDefaultMessageBody(e.target.value)}
            onBlur={() => {
              if (defaultMessageBody !== (settings.defaultMessageBody ?? "")) {
                void persist("defaultMessageBody", { defaultMessageBody: defaultMessageBody.trim() || undefined });
              }
            }}
            rows={2}
            placeholder="예: 알림 시간입니다"
            className="w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent"
          />
        </label>
        {savingField?.startsWith("default") ? (
          <p className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Save size={12} />저장 중…
          </p>
        ) : null}
      </SectionCard>

      {/* 채널 테스트 (REQ-039) */}
      <SectionCard
        title="채널 테스트"
        description="테스트 알림을 발송합니다. isTest 기록으로 남아 기록 탭에서 확인할 수 있습니다."
      >
        <div className="grid grid-cols-1 gap-2">
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void handleTest("push", ["push"])}
            disabled={testing !== null}
          >
            {testing === "push" ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            테스트 Push
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => void handleTest("telegram", ["telegram"])}
            disabled={testing !== null}
          >
            {testing === "telegram" ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            테스트 Telegram
          </Button>
          <Button
            size="sm"
            onClick={() => void handleTest("both", ["telegram", "push"])}
            disabled={testing !== null}
          >
            {testing === "both" ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            둘 다 테스트
          </Button>
        </div>

        {/* 진행 상황 스테퍼 (요구사항 1·2·3·6) */}
        <TestPushProgress phase={phase} />
        <TestChannelResults results={testResults} />
      </SectionCard>

      <ConfirmDialog
        open={removeConfirmOpen}
        title="기기 알림 등록 해제"
        description={`선택한 ${selectedDevices.size}개 기기의 알림 등록을 해제할까요?`}
        confirmLabel="알림 등록 해제"
        cancelLabel="취소"
        busy={removingDevices}
        onConfirm={() => void handleRemoveSelectedDevices()}
        onCancel={() => setRemoveConfirmOpen(false)}
      />

      <ConfirmDialog
        open={resetConfirmOpen}
        title="기기 알림 등록 초기화"
        description="모든 기기의 Push 알림 등록을 삭제합니다. 이후 알림을 받을 기기에서 다시 등록해야 합니다."
        confirmLabel="전체 초기화"
        cancelLabel="취소"
        busy={resettingDevices}
        onConfirm={() => void handleResetPushRegistrations()}
        onCancel={() => setResetConfirmOpen(false)}
      />
    </div>
  );
}
