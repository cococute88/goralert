"use client";

// GORALERT-ALERT-SYSTEM Layer B2 (REQ-045 / REQ-039)
// 설정 탭.
//   - Google 로그인: 현재 사용자 표시 + 로그아웃
//   - Telegram 설정: telegramChatId
//   - Push 설정: 이 기기 알림 등록(실제 FCM) + 등록된 기기 수 + 권한 상태
//   - 기본 알림 시간 / 기본 문구 / 전체 사용 토글
//   - 채널 테스트(REQ-039): 합성 AlertRule 로 alertProvider.sendTest 호출 → isTest 로그 기록

import { useEffect, useState } from "react";
import { LogOut, Loader2, Save, Send, Smartphone } from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertRule, AlertSettings, DeliveryChannel } from "@/lib/alerts/types";
import { loadAlertSettings, saveAlertSettings } from "@/lib/alerts/repositories";
import { isPushSupported, registerPushToken } from "@/lib/alerts/fcm-client";
import { alertProvider } from "@/lib/alerts/provider";
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

// 채널 테스트용 최소 합성 규칙 (REQ-039). 저장되지 않고 sendTest 에만 쓰인다.
function buildTestRule(uid: string, settings: AlertSettings, channels: DeliveryChannel[]): AlertRule {
  return {
    id: "settings-test",
    uid,
    kind: "date",
    name: "설정 테스트",
    enabled: true,
    condition: { kind: "date" },
    trigger: { mode: "once" },
    delivery: {
      channels,
      message: {
        title: settings.defaultMessageTitle?.trim() || "고라알림 테스트",
        body: settings.defaultMessageBody?.trim() || "설정 화면에서 보낸 테스트 알림입니다",
      },
    },
  };
}

export default function GoralertSettingsPage() {
  const toast = useToast();
  const { user, loading: authLoading, logout } = useFirebaseAuth();

  const [settings, setSettings] = useState<AlertSettings>({ globalEnabled: true });
  const [loading, setLoading] = useState(true);
  const [savingField, setSavingField] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);

  // Push 등록 상태.
  const [registering, setRegistering] = useState(false);
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

  const handleTest = async (key: string, channels: DeliveryChannel[]) => {
    if (!user) return;
    setTesting(key);
    try {
      const rule = buildTestRule(user.uid, settings, channels);
      const log = await alertProvider.sendTest(user.uid, rule, channels);
      const names = log.channels.map((c) => (c.channel === "telegram" ? "Telegram" : "Push")).join(", ");
      toast.success(`테스트 발송 완료 (${names || "채널 없음"}) · 기록 탭에서 확인하세요`);
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
          disabled={registering || pushSupported === false || permission === "denied"}
        >
          {registering ? <Loader2 size={14} className="animate-spin" /> : <Smartphone size={14} />}
          이 기기 알림 등록
        </Button>
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
