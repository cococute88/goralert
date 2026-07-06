// GORALERT-ALERT-SYSTEM
// Single shared client-side id generator. Previously duplicated three times
// (toast queue, ruleModel.buildRule/buildTemplate, clone.cloneRuleToDraft) with
// the same crypto.randomUUID + fallback logic. Centralized here so the fallback
// stays consistent and there is one place to change it.

export function generateId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}
