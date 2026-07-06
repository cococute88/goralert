// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// Pure helpers shared by the new/edit forms: which form to show for a given
// draft, and how to assemble a complete AlertRule from a working draft.

import type { AlertKind, AlertRule, AlertTemplate, Condition, DeliveryConfig, TriggerPolicy } from "@/lib/alerts/types";
import { generateId } from "@/lib/alerts/id";

// The UI form variants (Progressive Disclosure entry points).
export type FormKind = "date" | "ratio" | "metric" | "calendar" | "custom";

const METRIC_KINDS: AlertKind[] = ["rsi", "vix", "price", "fx", "gold", "bitcoin", "koreanEtf"];

// Korean labels for the type selector cards.
export const FORM_KIND_META: Record<FormKind, { label: string; description: string; emoji: string }> = {
  date: { label: "날짜 기반", description: "격주/매월/매주 정해진 날짜에 알림", emoji: "📅" },
  ratio: { label: "전환비", description: "두 종목 가격 비율이 기준을 넘으면 알림", emoji: "🔀" },
  metric: { label: "RSI · 지표", description: "RSI/VIX/가격 등 지표 기준 알림", emoji: "📈" },
  calendar: { label: "투자 캘린더", description: "배당락/매수마감 등 캘린더 일정 알림", emoji: "🗓️" },
  custom: { label: "사용자 정의", description: "직접 조건식을 작성", emoji: "🛠️" },
};

// Decide which form to render for an existing/cloned draft.
export function deriveFormKind(draft: Partial<AlertRule>): FormKind {
  const kind = draft.kind;
  const condition = draft.condition;
  if (kind === "ratio") return "ratio";
  if (kind === "custom") return "custom";
  if (kind && METRIC_KINDS.includes(kind)) return "metric";
  if (kind === "date") {
    const isCalendar =
      (condition?.kind === "date" && Boolean(condition.selector)) ||
      draft.trigger?.recurrence?.kind === "calendar";
    return isCalendar ? "calendar" : "date";
  }
  return "date";
}

// Default kind assigned when a brand-new draft is started for a form variant.
export function defaultKindForForm(formKind: FormKind): AlertKind {
  switch (formKind) {
    case "ratio":
      return "ratio";
    case "metric":
      return "rsi";
    case "custom":
      return "custom";
    case "calendar":
    case "date":
    default:
      return "date";
  }
}

// Assemble a complete AlertRule from a working draft. Timestamps are left to the
// repository (serverTimestamp). uid comes from the signed-in user.
export function buildRule(uid: string, draft: Partial<AlertRule>): AlertRule {
  const condition = (draft.condition ?? { kind: "date" }) as Condition;
  const kind = (draft.kind ?? condition.kind) as AlertKind;
  const trigger: TriggerPolicy = draft.trigger ?? { mode: "recurring" };
  const delivery: DeliveryConfig = {
    channels: draft.delivery?.channels?.length ? draft.delivery.channels : ["telegram", "push"],
    ...(draft.delivery?.message ? { message: draft.delivery.message } : {}),
  };

  return {
    id: draft.id ?? generateId(),
    uid,
    kind,
    name: (draft.name ?? "").trim(),
    enabled: draft.enabled ?? true,
    condition,
    trigger,
    delivery,
    ruleVersion: draft.ruleVersion ?? 1,
    ...(draft.lastTriggeredAt ? { lastTriggeredAt: draft.lastTriggeredAt } : {}),
    ...(draft.lastValue !== undefined ? { lastValue: draft.lastValue } : {}),
    ...(draft.createdAt ? { createdAt: draft.createdAt } : {}),
  };
}

// Build an AlertTemplate (⭐ 내 즐겨찾기, isBuiltIn:false) from the current draft.
export function buildTemplateFromDraft(draft: Partial<AlertRule>): AlertTemplate {
  const condition = (draft.condition ?? { kind: "date" }) as Condition;
  const kind = (draft.kind ?? condition.kind) as AlertKind;
  return {
    id: generateId(),
    name: (draft.name ?? "내 알림").trim() || "내 알림",
    kind,
    condition,
    trigger: draft.trigger ?? { mode: "recurring" },
    delivery: {
      channels: draft.delivery?.channels?.length ? draft.delivery.channels : ["telegram", "push"],
      ...(draft.delivery?.message ? { message: draft.delivery.message } : {}),
    },
    isBuiltIn: false,
  };
}
