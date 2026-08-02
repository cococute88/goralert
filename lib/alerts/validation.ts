// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// Pure validators for alert rules and notification logs. No I/O — these return
// { ok, errors } so callers (UI / repositories / future engine) can decide how
// to surface problems.

import type {
  AlertKind,
  AlertPriority,
  AlertSeverity,
  Comparator,
  Condition,
  DeliveryChannel,
  NotificationLog,
  AlertRule,
} from "./types";

export type ValidationResult = { ok: boolean; errors: string[] };

export const ALERT_KINDS: readonly AlertKind[] = [
  "date",
  "ratio",
  "dividend",
  "rsi",
  "vix",
  "price",
  "fx",
  "gold",
  "bitcoin",
  "koreanEtf",
  "custom",
  "composite",
];

export const COMPARATORS: readonly Comparator[] = ["gt", "gte", "lt", "lte", "eq", "crossUp", "crossDown"];

export const DELIVERY_CHANNELS: readonly DeliveryChannel[] = ["telegram", "push"];

export const ALERT_PRIORITIES: readonly AlertPriority[] = ["high", "normal", "low"];

export const ALERT_SEVERITIES: readonly AlertSeverity[] = ["critical", "warning", "info"];

// Kinds whose condition carries a numeric threshold + comparator.
const THRESHOLD_KINDS: readonly AlertKind[] = ["ratio", "rsi", "vix", "price", "fx", "gold", "bitcoin", "koreanEtf"];

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isFiniteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isIsoTimestamp(value: unknown): boolean {
  if (typeof value !== "string" || !value.trim()) return false;
  const parsed = new Date(value);
  return Number.isFinite(parsed.getTime());
}

function conditionHasThreshold(condition: Condition): condition is Extract<Condition, { threshold: number; comparator: Comparator }> {
  return "threshold" in condition && "comparator" in condition;
}

function validateConditionInputs(condition: Condition, errors: string[], path = "condition"): void {
  const metric = "metric" in condition ? condition.metric : undefined;
  const metricTicker = metric && "ticker" in metric ? metric.ticker : undefined;
  if (condition.kind === "ratio") {
    if (!isNonEmptyString(condition.numerator)) errors.push(`${path}.numerator is required`);
    if (!isNonEmptyString(condition.denominator)) errors.push(`${path}.denominator is required`);
  }
  if (["rsi", "price"].includes(condition.kind)) {
    if (!isNonEmptyString(metricTicker)) errors.push(`${path}.metric.ticker is required`);
  }
  if (condition.kind === "rsi" && (
    metric?.metric !== "rsi" || !Number.isInteger(metric.period) || metric.period < 1
  )) {
    errors.push(`${path}.metric.period must be an integer >= 1`);
  }
  if (condition.kind === "fx" && !isNonEmptyString(metric?.metric === "fx" ? metric.pair : undefined)) {
    errors.push(`${path}.metric.pair is required`);
  }
  if (condition.kind === "koreanEtf" && !isNonEmptyString(metric?.metric === "koreanEtf" ? metric.code : undefined)) {
    errors.push(`${path}.metric.code is required`);
  }
  if (["rsi", "vix", "price", "fx", "gold", "bitcoin", "koreanEtf"].includes(condition.kind)) {
    if (metric?.metric !== condition.kind) {
      errors.push(`${path}.metric.metric must match ${condition.kind}`);
    }
  }
  if (condition.kind === "custom" && !isNonEmptyString(condition.expression)) {
    errors.push(`${path}.expression is required`);
  }
  if (condition.kind === "composite") {
    if (!["and", "or"].includes(condition.operator)) errors.push(`${path}.operator must be and or or`);
    if (condition.conditions.length === 0) errors.push(`${path}.conditions must not be empty`);
    condition.conditions.forEach((child, index) => validateConditionInputs(child, errors, `${path}.conditions[${index}]`));
  }
}

export function validateAlertRule(rule: AlertRule): ValidationResult {
  const errors: string[] = [];

  if (!isNonEmptyString(rule.name)) {
    errors.push("name must be a non-empty string");
  }

  if (!ALERT_KINDS.includes(rule.kind)) {
    errors.push(`kind must be one of: ${ALERT_KINDS.join(", ")}`);
  }

  if (!rule.condition || typeof rule.condition !== "object") {
    errors.push("condition is required");
  } else {
    // For non-composite rules the rule kind must match the condition kind.
    if (rule.kind !== "composite" && rule.condition.kind !== rule.kind) {
      errors.push(`condition.kind (${rule.condition.kind}) must match rule.kind (${rule.kind}) for non-composite rules`);
    }
    if (rule.kind === "composite" && rule.condition.kind !== "composite") {
      errors.push("condition.kind must be composite when rule.kind is composite");
    }
    validateConditionInputs(rule.condition, errors);

    // Threshold-based kinds need a finite threshold and an allowed comparator.
    if (THRESHOLD_KINDS.includes(rule.kind)) {
      if (!conditionHasThreshold(rule.condition)) {
        errors.push(`condition for kind '${rule.kind}' must include threshold and comparator`);
      } else {
        if (!isFiniteNumber(rule.condition.threshold)) {
          errors.push("condition.threshold must be a finite number");
        }
        if (!COMPARATORS.includes(rule.condition.comparator)) {
          errors.push(`condition.comparator must be one of: ${COMPARATORS.join(", ")}`);
        }
      }
    }
  }

  // Delivery channels: non-empty subset of allowed channels.
  if (!rule.delivery || !Array.isArray(rule.delivery.channels) || rule.delivery.channels.length === 0) {
    errors.push("delivery.channels must be a non-empty array");
  } else {
    for (const channel of rule.delivery.channels) {
      if (!DELIVERY_CHANNELS.includes(channel)) {
        errors.push(`delivery.channels contains invalid channel '${String(channel)}'`);
      }
    }
  }

  // cooldownMinutes >= 0 when present.
  if (rule.trigger?.cooldownMinutes !== undefined) {
    if (!isFiniteNumber(rule.trigger.cooldownMinutes) || rule.trigger.cooldownMinutes < 0) {
      errors.push("trigger.cooldownMinutes must be a number >= 0");
    }
  }

  // ruleVersion: non-negative integer if present.
  if (rule.ruleVersion !== undefined) {
    if (!Number.isInteger(rule.ruleVersion) || rule.ruleVersion < 0) {
      errors.push("ruleVersion must be a non-negative integer");
    }
  }

  // engineVersion: string if present.
  if (rule.engineVersion !== undefined && typeof rule.engineVersion !== "string") {
    errors.push("engineVersion must be a string");
  }

  return { ok: errors.length === 0, errors };
}

export function validateNotificationLog(log: NotificationLog): ValidationResult {
  const errors: string[] = [];

  if (!isNonEmptyString(log.id)) errors.push("id is required");
  if (!isNonEmptyString(log.eventId)) errors.push("eventId is required");
  if (!isNonEmptyString(log.ruleId)) errors.push("ruleId is required");

  if (!ALERT_KINDS.includes(log.kind)) {
    errors.push(`kind must be one of: ${ALERT_KINDS.join(", ")}`);
  }

  if (!isIsoTimestamp(log.firedAt)) errors.push("firedAt must be an ISO timestamp");
  if (!isIsoTimestamp(log.evaluatedAt)) errors.push("evaluatedAt must be an ISO timestamp");
  if (log.sentAt !== undefined && !isIsoTimestamp(log.sentAt)) {
    errors.push("sentAt must be an ISO timestamp when present");
  }

  if (!log.message || !isNonEmptyString(log.message.title) || typeof log.message.body !== "string") {
    errors.push("message.title (non-empty) and message.body are required");
  }

  if (!Array.isArray(log.channels)) {
    errors.push("channels must be an array");
  } else {
    for (const entry of log.channels) {
      if (!DELIVERY_CHANNELS.includes(entry.channel)) {
        errors.push(`channels contains invalid channel '${String(entry.channel)}'`);
      }
      if (!["pending", "sending", "sent", "failed", "unknown"].includes(entry.status)) {
        errors.push("channel.status is invalid");
      }
    }
  }

  if (log.scheduledFor !== undefined && !isIsoTimestamp(log.scheduledFor)) {
    errors.push("scheduledFor must be an ISO timestamp when present");
  }
  if (log.processingStartedAt !== undefined && !isIsoTimestamp(log.processingStartedAt)) {
    errors.push("processingStartedAt must be an ISO timestamp when present");
  }
  if (log.completedAt !== undefined && !isIsoTimestamp(log.completedAt)) {
    errors.push("completedAt must be an ISO timestamp when present");
  }

  if (typeof log.isTest !== "boolean") errors.push("isTest must be a boolean");

  if (log.priority !== undefined && !ALERT_PRIORITIES.includes(log.priority)) {
    errors.push(`priority must be one of: ${ALERT_PRIORITIES.join(", ")}`);
  }
  if (log.severity !== undefined && !ALERT_SEVERITIES.includes(log.severity)) {
    errors.push(`severity must be one of: ${ALERT_SEVERITIES.join(", ")}`);
  }

  return { ok: errors.length === 0, errors };
}
