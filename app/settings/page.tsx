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
import { LogOut, Loader2, Save, Send, Smartphone, BellOff } from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type {
  AlertSettings,
  DeliveryChannel,
  MessageTemplate,
} from "@/lib/alerts/types";
import {
  enqueueTestPushRequest,
  loadAlertSettings,
  saveAlertSettings,
  waitForTestPushResult,
} from "@/lib/alerts/repositories";
import {
  isPushSupported,
  registerPushToken,
  unregisterPushToken,
} from "@/lib/alerts/fcm-client";
import { dispatchTestPushWorkflow } from "@/lib/alerts/test-push";
import { Badge, Button, Card, CardSection, Toggle } from "@/components/alerts/ui";
import { useToast } from "@/components/alerts/ui/toast";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";

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

  // Push 등록 상태.
  const [registering, setRegistering] = useState(false);
  const [unregistering, setUnregistering] = useState(false);
  const [pushSupported, setPushSupported] = useState<boolean | null>(null);
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">("default");

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
      .then((loaded) => {
        if (!active) return;
        setSettings(loaded);
        setTelegramChatId(loaded.telegramChatId ?? "");
        setDefaultAlertTime(loaded.defaultAlertTime ?? "");
        setDefaultMessageTitle(loaded.defaultMessageTitle ?? "");
        setDefaultMessageBody(loaded.defaultMessageBody ?? "");
      })
      .catch(() => {
        if (active) setSettings({ globalEnabled: true });
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user]);

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
      if (result.ok && result.token) {
        // 등록 성공 — 로컬 상태도 갱신(dedupe).
        setSettings((prev) => {
          const current = prev.pushTokens ?? [];
          if (current.includes(result.token!)) return prev;
          return { ...prev, pushTokens: [...current, result.token!] };
        });
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

  const handleUnregisterToken = async () => {
    if (!user) return;
    setUnregistering(true);
    try {
      const result = await unregisterPushToken(user.uid);
      if (result.ok) {
        // 등록 해제 성공 — 이 기기 토큰을 로컬 상태에서도 제거.
        setSettings((prev) => {
          if (!prev.pushTokens?.length) return prev;
          const next = result.token
            ? prev.pushTokens.filter((t) => t !== result.token)
            : prev.pushTokens.slice(0, -1);
          return { ...prev, pushTokens: next };
        });
        toast.success("이 기기 알림 등록을 해제했어요");
      } else {
        toast.error(result.error ?? "알림 등록 해제에 실패했습니다");
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "알림 등록 해제에 실패했습니다");
    } finally {
      setUnregistering(false);
    }
  };

  // Test sends go through the SAME production path as scheduled alerts:
  //   1) ENQUEUE users/{uid}/testPushRequests (no in-browser delivery)
  //   2) fire the alert-test-push workflow immediately (thin /api/test-push bridge)
  //   3) the Python engine drains it via send_test_alert -> deliver -> PushChannel
  //      and writes the isTest NotificationLog + the request result
  //   4) observe the request doc for the engine's REAL channel result (no fake "sent")
  const handleTest = async (key: string, channels: DeliveryChannel[]) => {
    if (!user) return;
    setTesting(key);
    try {
      const message: MessageTemplate = {
        title: settings.defaultMessageTitle?.trim() || "고라알림 테스트",
        body: settings.defaultMessageBody?.trim() || "설정 화면에서 보낸 테스트 알림입니다",
      };
      const label = (c: DeliveryChannel) => (c === "telegram" ? "Telegram" : "Push");
      const requestId = await enqueueTestPushRequest(user.uid, { channels, message });

      // Trigger the engine immediately instead of waiting for the cron.
      const dispatch = await dispatchTestPushWorkflow(user);
      if (!dispatch.ok) {
        // Still enqueued — the 5-min cron will pick it up — but tell the user the
        // immediate trigger failed (req 6).
        toast.error(`즉시 발송 트리거 실패 — ${dispatch.error} · 잠시 후 자동 처리됩니다`);
      } else {
        toast.show("테스트 발송을 시작했어요 · 결과를 기다리는 중…", "info");
      }

      const result = await waitForTestPushResult(user.uid, requestId);
      if (!result) {
        toast.error("아직 결과가 확인되지 않았어요 · 잠시 후 기록 탭에서 확인하세요");
        return;
      }

      const results = result.results ?? [];
      const failed = results.filter((c) => c.status === "failed");
      const sent = results.filter((c) => c.status === "sent");
      if (result.status !== "done" || failed.length > 0) {
        const detail = failed[0]?.error ? ` — ${failed[0].error}` : result.error ? ` — ${result.error}` : "";
        const who = failed.map((c) => label(c.channel)).join(", ") || channels.map(label).join(", ");
        toast.error(`${who} 발송 실패${detail} · 기록 탭에서 확인하세요`);
      } else {
        toast.success(`테스트 발송 완료 (${sent.map((c) => label(c.channel)).join(", ") || "채널 없음"}) · 기록 탭에서 확인하세요`);
      }
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "테스트 발송에 실패했습니다");
    } finally {
      setTesting(null);
    }
  };

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;

  const pushCount = settings.pushTokens?.length ?? 0;
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
          <span className="text-sm text-foreground">알림 권한</span>
          <Badge tone={permissionTone}>{permissionLabel}</Badge>
        </div>
        <Button
          variant="secondary"
          size="sm"
          className="w-full"
          onClick={() => void handleRegisterToken()}
          disabled={registering || unregistering || pushSupported === false || permission === "denied"}
        >
          {registering ? <Loader2 size={14} className="animate-spin" /> : <Smartphone size={14} />}
          이 기기 알림 등록
        </Button>
        {pushCount > 0 ? (
          <Button
            variant="ghost"
            size="sm"
            className="w-full"
            onClick={() => void handleUnregisterToken()}
            disabled={registering || unregistering || pushSupported === false}
          >
            {unregistering ? <Loader2 size={14} className="animate-spin" /> : <BellOff size={14} />}
            이 기기 등록 해제
          </Button>
        ) : null}
        {pushSupported === false ? (
          <p className="text-[11px] text-muted-foreground">
            이 브라우저에서는 푸시 알림을 사용할 수 없습니다.
          </p>
        ) : permission === "denied" ? (
          <p className="text-[11px] text-danger">
            알림 권한이 차단되어 있습니다. 브라우저 설정에서 알림을 허용한 뒤 다시 시도하세요.
          </p>
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
      </SectionCard>
    </div>
  );
}
