"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-042/046)
// Shared rule editor used by both the create (new) and edit pages. It renders:
//   1) 알림 이름
//   2) the per-kind form (Progressive Disclosure — only relevant fields)
//   3) 공통 후행 필드: 전송 채널 + 메시지
//   4) "고급" 디스클로저: 쿨다운/방해 금지 시간
//   5) 저장 / 즐겨찾기로 저장 + 검증 에러
// The draft state is owned by the parent so clone/prefill works.

import { useState } from "react";
import { ChevronDown, ChevronUp, Loader2, Star } from "lucide-react";
import type { AlertRule } from "@/lib/alerts/types";
import { saveAlertRule, saveAlertTemplate } from "@/lib/alerts/repositories";
import { validateAlertRule } from "@/lib/alerts/validation";
import { Button } from "../ui";
import { useToast } from "../ui/toast";
import { Field, TextInput, type RuleFormProps } from "./fields";
import DateScheduleForm from "./DateScheduleForm";
import RatioForm from "./RatioForm";
import MetricForm from "./MetricForm";
import CalendarDateForm from "./CalendarDateForm";
import CustomForm from "./CustomForm";
import DeliveryChannelsField from "./DeliveryChannelsField";
import MessageTemplateField from "./MessageTemplateField";
import AdvancedTriggerFields from "./AdvancedTriggerFields";
import SingleCalendarEventForm from "./SingleCalendarEventForm";
import { buildRule, buildTemplateFromDraft, type FormKind } from "./ruleModel";
import { ruleFormVisibility, type RuleFormMode } from "./rule-form-mode";
import { isSingleCalendarEventOccurrenceFuture } from "@/lib/calendar-alerts";

function PerKindForm({ formKind, value, onChange }: { formKind: FormKind } & RuleFormProps) {
  switch (formKind) {
    case "ratio":
      return <RatioForm value={value} onChange={onChange} />;
    case "metric":
      return <MetricForm value={value} onChange={onChange} />;
    case "calendar":
      return <CalendarDateForm value={value} onChange={onChange} />;
    case "custom":
      return <CustomForm value={value} onChange={onChange} />;
    case "date":
    default:
      return <DateScheduleForm value={value} onChange={onChange} />;
  }
}

export default function RuleForm({
  uid,
  formKind,
  draft,
  setDraft,
  submitLabel,
  onSaved,
  mode = "standard",
}: {
  uid: string;
  formKind: FormKind;
  draft: Partial<AlertRule>;
  setDraft: (next: Partial<AlertRule>) => void;
  submitLabel: string;
  onSaved: () => void;
  mode?: RuleFormMode;
}) {
  const toast = useToast();
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);
  const [saving, setSaving] = useState(false);
  const [savingFavorite, setSavingFavorite] = useState(false);
  const visibility = ruleFormVisibility(mode);

  const handleSave = async () => {
    if (mode === "single-calendar-event" && !isSingleCalendarEventOccurrenceFuture(draft)) {
      const message = "선택한 일정의 알림 시각이 이미 지났습니다";
      setErrors([message]);
      toast.error(message);
      return;
    }
    const rule = buildRule(uid, draft);
    const result = validateAlertRule(rule);
    if (!result.ok) {
      setErrors(result.errors);
      toast.error("입력값을 확인해주세요");
      return;
    }
    setErrors([]);
    setSaving(true);
    try {
      await saveAlertRule(uid, rule);
      toast.success("알림을 저장했습니다");
      onSaved();
    } catch (err) {
      // Keep the draft in place and avoid exposing Firestore paths or a UID in
      // user-facing UI. The browser console keeps a safe diagnostic breadcrumb.
      console.error("[alerts] rule save failed", { error: err instanceof Error ? err.name : "unknown" });
      const processingMessage = "처리 중인 알림은 현재 회차가 끝난 뒤 일정을 변경할 수 있습니다.";
      toast.error(err instanceof Error && err.message === processingMessage
        ? processingMessage
        : "알림 저장에 실패했습니다. 잠시 후 다시 시도해 주세요.");
    } finally {
      setSaving(false);
    }
  };

  const handleSaveFavorite = async () => {
    if (!(draft.name ?? "").trim()) {
      toast.error("즐겨찾기 저장 전에 이름을 입력하세요");
      return;
    }
    setSavingFavorite(true);
    try {
      await saveAlertTemplate(uid, buildTemplateFromDraft(draft));
      toast.success("즐겨찾기에 저장했습니다");
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "즐겨찾기 저장에 실패했습니다");
    } finally {
      setSavingFavorite(false);
    }
  };

  return (
    <div className="space-y-4">
      <Field label="알림 이름">
        <TextInput
          value={draft.name ?? ""}
          placeholder="예: VR 주문"
          autoFocus
          onChange={(e) => setDraft({ ...draft, name: e.target.value })}
        />
      </Field>

      {visibility.singleEventSummary ? (
        <SingleCalendarEventForm value={draft} onChange={setDraft} />
      ) : visibility.perKindFilters ? (
        <PerKindForm formKind={formKind} value={draft} onChange={setDraft} />
      ) : null}

      <div className="h-px bg-border" />

      <DeliveryChannelsField value={draft} onChange={setDraft} />
      {visibility.messageTemplate ? <MessageTemplateField value={draft} onChange={setDraft} /> : null}

      {visibility.advancedSettings ? (
        <>
          <button
            type="button"
            onClick={() => setShowAdvanced((prev) => !prev)}
            className="flex w-full items-center justify-between rounded-xl border border-border bg-card px-3 py-2 text-sm font-medium text-foreground"
          >
            고급 설정
            {showAdvanced ? <ChevronUp size={16} /> : <ChevronDown size={16} />}
          </button>
          {showAdvanced ? <AdvancedTriggerFields value={draft} onChange={setDraft} /> : null}
        </>
      ) : null}

      {errors.length > 0 ? (
        <ul className="space-y-1 rounded-xl border border-danger/40 bg-danger/10 px-3 py-2 text-[11px] text-danger">
          {errors.map((error) => (
            <li key={error}>• {error}</li>
          ))}
        </ul>
      ) : null}

      <div className="flex flex-col gap-2">
        <Button onClick={handleSave} disabled={saving} className="w-full">
          {saving ? <Loader2 size={16} className="animate-spin" /> : null}
          {submitLabel}
        </Button>
        {visibility.favoriteAction ? (
          <Button variant="secondary" onClick={handleSaveFavorite} disabled={savingFavorite} className="w-full">
            {savingFavorite ? <Loader2 size={16} className="animate-spin" /> : <Star size={16} />}
            즐겨찾기로 저장
          </Button>
        ) : null}
      </div>
    </div>
  );
}
