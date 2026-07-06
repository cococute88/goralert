// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// Tiny sessionStorage bridge to hand a cloned/prefilled draft to the create page
// (e.g. 복제 from the alerts list). Cleared after it is consumed once.

import type { AlertRule } from "@/lib/alerts/types";

const KEY = "goralert:new-rule-draft";

export function stashDraft(draft: Partial<AlertRule>): void {
  try {
    sessionStorage.setItem(KEY, JSON.stringify(draft));
  } catch {
    // sessionStorage unavailable — ignore; create page will start blank.
  }
}

export function takeDraft(): Partial<AlertRule> | null {
  try {
    const raw = sessionStorage.getItem(KEY);
    if (!raw) return null;
    sessionStorage.removeItem(KEY);
    return JSON.parse(raw) as Partial<AlertRule>;
  } catch {
    return null;
  }
}
