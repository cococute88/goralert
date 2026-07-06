"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-042/046)
// 새 알림 만들기 — Progressive Disclosure.
//   Step 1: 종류 선택 (AlertTypeSelector). 템플릿 선택 시 갤러리 표시.
//   Step 2: 선택한 종류에 필요한 필드만 보이는 RuleForm.
// 목록에서 "복제"로 넘어온 경우 sessionStorage 의 draft 를 읽어 바로 Step 2 로 진입.

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import type { AlertRule, AlertSettings } from "@/lib/alerts/types";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import { loadAlertSettings } from "@/lib/alerts/repositories";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import AlertTypeSelector, { type TypeChoice } from "@/components/alerts/forms/AlertTypeSelector";
import TemplateGallery from "@/components/alerts/forms/TemplateGallery";
import RuleForm from "@/components/alerts/forms/RuleForm";
import { deriveFormKind, defaultKindForForm, type FormKind } from "@/components/alerts/forms/ruleModel";
import { takeDraft } from "@/components/alerts/draftStore";

type Step = "select" | "template" | "form";

const SEOUL_TZ = "Asia/Seoul";

// Build the initial recurrence for a fresh draft, applying the user's
// 기본 알림 시간(defaultAlertTime) ONLY where the form's concept is "알림 시각":
//   - date     → biweekly Saturday (matches DateScheduleForm default) at that time
//   - calendar → calendar-driven send at that time (matches CalendarDateForm)
// metric("확인 시각": 종가 확인 등 특수 의미) / ratio / custom(임계값·식, 시각 없음)
// 은 기본 시간을 적용하지 않는다(폼 의미와 불일치 → 안전하지 않음).
function initialRecurrence(formKind: FormKind, time: string | undefined): AlertRule["trigger"]["recurrence"] | undefined {
  if (!time) return undefined;
  if (formKind === "date") return { kind: "biweekly", weekday: 6, time, tz: SEOUL_TZ };
  if (formKind === "calendar") return { kind: "calendar", time, tz: SEOUL_TZ };
  return undefined;
}

// A fresh draft for the chosen kind. Pre-fills the shared message and (where it
// makes sense) the alert time from the user's "기본 알림 설정" so those settings
// actually take effect on new alerts.
function blankDraft(formKind: FormKind, settings?: AlertSettings): Partial<AlertRule> {
  const title = settings?.defaultMessageTitle?.trim();
  const body = settings?.defaultMessageBody?.trim();
  const hasDefaultMessage = Boolean(title || body);
  const recurrence = initialRecurrence(formKind, settings?.defaultAlertTime?.trim() || undefined);
  return {
    kind: defaultKindForForm(formKind),
    name: "",
    enabled: true,
    delivery: {
      channels: ["telegram", "push"],
      ...(hasDefaultMessage ? { message: { title: title ?? "", body: body ?? "" } } : {}),
    },
    trigger: { mode: "recurring", ...(recurrence ? { recurrence } : {}) },
  };
}

export default function NewAlertPage() {
  const router = useRouter();
  const { user, loading: authLoading } = useFirebaseAuth();
  const [step, setStep] = useState<Step>("select");
  const [formKind, setFormKind] = useState<FormKind>("date");
  const [draft, setDraft] = useState<Partial<AlertRule>>({});
  const [settings, setSettings] = useState<AlertSettings | undefined>(undefined);
  const [ready, setReady] = useState(false);

  // Consume a cloned/prefilled draft (from the list 복제 action) on mount.
  // If no draft but the URL requested the template gallery (즐겨찾기 진입 단축
  // from the alerts list), open it directly. Reads window.location to avoid the
  // useSearchParams Suspense requirement.
  useEffect(() => {
    const stashed = takeDraft();
    if (stashed) {
      setDraft(stashed);
      setFormKind(deriveFormKind(stashed));
      setStep("form");
    } else if (typeof window !== "undefined" && new URLSearchParams(window.location.search).get("start") === "template") {
      setStep("template");
    }
    setReady(true);
  }, []);

  // Load the user's default-message settings in the background so a freshly
  // started (non-cloned) draft can be pre-filled. Failure is non-blocking.
  useEffect(() => {
    if (!user) return;
    let active = true;
    loadAlertSettings(user.uid)
      .then((loaded) => {
        if (active) setSettings(loaded);
      })
      .catch(() => {
        /* defaults simply won't be applied */
      });
    return () => {
      active = false;
    };
  }, [user]);

  if (authLoading || !ready) return <LoadingState />;
  if (!user) return <NoUserState />;

  const handleSelect = (choice: TypeChoice) => {
    if (choice === "template") {
      setStep("template");
      return;
    }
    setFormKind(choice);
    setDraft(blankDraft(choice, settings));
    setStep("form");
  };

  const handlePickTemplate = (picked: Partial<AlertRule>) => {
    setDraft(picked);
    setFormKind(deriveFormKind(picked));
    setStep("form");
  };

  const goBack = () => {
    if (step === "form") setStep("select");
    else if (step === "template") setStep("select");
    else router.push("/alerts");
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <button type="button" onClick={goBack} className="rounded-lg p-1 text-muted-foreground hover:text-foreground" aria-label="뒤로">
          <ChevronLeft size={20} />
        </button>
        <h1 className="text-lg font-bold text-foreground">
          {step === "select" ? "새 알림 만들기" : step === "template" ? "템플릿 선택" : "알림 설정"}
        </h1>
      </div>

      {step === "select" ? <AlertTypeSelector onSelect={handleSelect} /> : null}
      {step === "template" ? <TemplateGallery uid={user.uid} onPick={handlePickTemplate} /> : null}
      {step === "form" ? (
        <RuleForm
          uid={user.uid}
          formKind={formKind}
          draft={draft}
          setDraft={setDraft}
          submitLabel="알림 저장"
          onSaved={() => router.push("/alerts")}
        />
      ) : null}
    </div>
  );
}
