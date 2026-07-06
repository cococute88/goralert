// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// cloneRuleToDraft powers the Favorite/Template "새 알림 만들기" flow: it turns an
// existing AlertRule or AlertTemplate into a fresh new-rule draft. The source is
// never mutated.

import type { AlertRule, AlertTemplate } from "./types";
import { generateId } from "./id";

// Deep clone via structured serialization so nested condition/trigger/delivery
// objects are copied (not shared) and the source stays untouched.
function deepCopy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function cloneRuleToDraft(source: AlertRule | AlertTemplate): Partial<AlertRule> {
  return {
    id: generateId(),
    kind: source.kind,
    name: source.name,
    enabled: true,
    condition: deepCopy(source.condition),
    trigger: deepCopy(source.trigger),
    delivery: deepCopy(source.delivery),
    // Freshly cloned drafts have no trigger history.
    lastTriggeredAt: undefined,
    lastValue: undefined,
  };
}
