// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// Domain types for the alert system. These mirror the spec requirements
// (US-001..US-008, REQ-024/044) and are written so the future Python engine
// can consume the same persisted shapes without changes.

export type AlertKind =
  | "date"
  | "ratio"
  | "dividend"
  | "rsi"
  | "vix"
  | "price"
  | "fx"
  | "gold"
  | "bitcoin"
  | "koreanEtf"
  | "custom"
  | "composite";

export type Comparator = "gt" | "gte" | "lt" | "lte" | "eq" | "crossUp" | "crossDown";

// Identifies a single measurable market metric. Used by metric/ratio conditions
// and by the (future) Python engine to know which data series to evaluate.
export type MetricId =
  | { metric: "rsi"; ticker: string; period: number }
  | { metric: "vix" }
  | { metric: "price"; ticker: string }
  | { metric: "fx"; pair: string }
  | { metric: "gold" }
  | { metric: "bitcoin" }
  | { metric: "koreanEtf"; code: string };

export type CalendarMark = "star" | "heart";

// Selects calendar events that should drive a date alert.
// markFilter limits to 별(star)/하트(heart) 표시 종목 (US-006/US-007).
export type DateEventSelector = {
  source: "calendarCustomEvents" | "calendarEvents";
  match?: {
    ticker?: string;
    // Legacy rules used one string. New rules can select multiple event types.
    type?: string | string[];
    titleContains?: string;
  };
  markFilter?: CalendarMark[];
};

// --- Condition union ---------------------------------------------------------

// A recurring/scheduled date alert (US-001/002/003 + calendar times US-006/007/008).
// The actual recurrence cadence lives in TriggerPolicy.recurrence; this condition
// only describes *which* calendar source/selector (if any) is involved.
export type DateCondition = {
  kind: "date";
  selector?: DateEventSelector;
};

// Compares the ratio of two metrics/tickers against a threshold (US-004/005).
export type RatioCondition = {
  kind: "ratio";
  numerator: string;
  denominator: string;
  comparator: Comparator;
  threshold: number;
};

// Dividend-related trigger (e.g. ex-dividend proximity, payout change).
export type DividendCondition = {
  kind: "dividend";
  ticker: string;
  comparator?: Comparator;
  threshold?: number;
  selector?: DateEventSelector;
};

// Threshold comparison on a single market metric (rsi/vix/price/fx/gold/bitcoin/koreanEtf).
// kind here mirrors the metric so AlertRule.kind === condition.kind for non-composite rules.
export type MetricCondition = {
  kind: "rsi" | "vix" | "price" | "fx" | "gold" | "bitcoin" | "koreanEtf";
  metric: MetricId;
  comparator: Comparator;
  threshold: number;
};

// Free-form user condition that the engine interprets (escape hatch).
export type CustomCondition = {
  kind: "custom";
  expression: string;
  params?: Record<string, unknown>;
};

// Logical combination of child conditions.
export type CompositeCondition = {
  kind: "composite";
  operator: "and" | "or";
  conditions: Condition[];
};

export type Condition =
  | DateCondition
  | RatioCondition
  | DividendCondition
  | MetricCondition
  | CustomCondition
  | CompositeCondition;

// --- Trigger policy ----------------------------------------------------------

export type QuietHours = {
  start: string; // HH:mm
  end: string; // HH:mm
  tz: string;
};

// Expresses the recurring cadence for date/scheduled alerts.
// - biweekly: every two weeks (US-001 VR 주문, Saturday)
// - monthlyLastDay: last calendar day of the month (US-002)
// - monthlyFirstDay: first calendar day of the month (US-003)
// - weekly: a specific weekday
// - calendar: driven by calendar events (US-006/007/008 fire at a fixed time)
export type Recurrence = {
  kind: "biweekly" | "monthlyLastDay" | "monthlyFirstDay" | "weekly" | "calendar";
  weekday?: number; // 0=Sunday .. 6=Saturday
  time?: string; // HH:mm
  tz?: string; // default Asia/Seoul
  anchorDate?: string; // ISO date used to anchor biweekly cadence
};

export type TriggerPolicy = {
  mode: "once" | "recurring";
  cooldownMinutes?: number;
  quietHours?: QuietHours;
  recurrence?: Recurrence;
};

// --- Delivery ----------------------------------------------------------------

export type DeliveryChannel = "telegram" | "push";

export type MessageTemplate = {
  title: string;
  body: string;
};

export type DeliveryConfig = {
  channels: DeliveryChannel[];
  message?: MessageTemplate;
};

export type AlertPriority = "high" | "normal" | "low";
export type AlertSeverity = "critical" | "warning" | "info";

// --- Persisted entities ------------------------------------------------------

export type AlertRule = {
  id: string;
  uid: string;
  kind: AlertKind;
  name: string;
  enabled: boolean;
  condition: Condition;
  trigger: TriggerPolicy;
  delivery: DeliveryConfig;
  lastTriggeredAt?: string;
  lastValue?: number | string;
  ruleVersion?: number;
  engineVersion?: string;
  createdAt?: unknown;
  updatedAt?: unknown;
};

export type AlertEvent = {
  eventId: string;
  ruleId: string;
  uid: string;
  kind: AlertKind;
  value?: number | string;
  message: MessageTemplate;
  evaluatedAt: string;
  sentAt?: string;
  firedAt: string;
  priority?: AlertPriority;
  severity?: AlertSeverity;
};

export type NotificationChannelResult = {
  channel: DeliveryChannel;
  status: "sent" | "failed";
  error?: string;
};

// Permanent history record (永久 보존, never deleted — UI windows the view only).
// ruleName/tickers are denormalized search-key fields for REQ-024/REQ-044 history search.
export type NotificationLog = {
  id: string;
  eventId: string;
  ruleId: string;
  kind: AlertKind;
  firedAt: string;
  evaluatedAt: string;
  sentAt?: string;
  evaluatedValue?: number | string;
  message: MessageTemplate;
  channels: NotificationChannelResult[];
  isTest: boolean;
  priority?: AlertPriority;
  severity?: AlertSeverity;
  ruleName?: string;
  tickers?: string[];
  createdAt?: unknown;
};

export type AlertSettings = {
  globalEnabled: boolean;
  telegramChatId?: string;
  pushTokens?: string[];
  defaultQuietHours?: QuietHours;
  defaultAlertTime?: string; // HH:mm
  defaultMessageTitle?: string;
  defaultMessageBody?: string;
  updatedAt?: unknown;
};

// isBuiltIn distinguishes 📦 기본 템플릿 (DEFAULT_TEMPLATES) vs ⭐ 내 즐겨찾기 (user-saved).
export type AlertTemplate = {
  id: string;
  name: string;
  kind: AlertKind;
  condition: Condition;
  trigger: TriggerPolicy;
  delivery: DeliveryConfig;
  isBuiltIn?: boolean;
  createdAt?: unknown;
};

// Goralert-owned 🔔 custom mark. NOTE: ⭐(star)/❤️(heart) marks remain read from
// the existing calendar data (calendarEvents meta); this entity only persists the
// new bell mark that the alert system owns.
export type CalendarAlertMark = {
  id: string;
  ticker?: string;
  date?: string;
  eventId?: string;
  markType: "bell";
  note?: string;
  createdAt?: unknown;
  updatedAt?: unknown;
};
