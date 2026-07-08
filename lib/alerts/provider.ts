// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// Swappable engine boundary. The whole alert system talks to the engine through
// the AlertProvider interface so the future Python engine can drop in without
// touching the UI or repository layers.
//
// ENGINE STATUS (Sprint 2): the REAL alert engine is now the Python package
// `alert_engine/` running on a GitHub Actions cron (see
// .github/workflows/alert-engine.yml). It owns evaluation, gating, delivery,
// and writing NotificationLogs to Firestore (the single source of truth).
// This web `alertProvider` remains an in-app convenience for PREVIEW only
// (`evaluatePreview`). Test SENDS no longer go through `sendTest`: the settings
// and alerts screens enqueue a request (repositories.enqueueTestPushRequest)
// that the Python engine delivers through the SAME production path as scheduled
// alerts (send_test_alert -> deliver -> PushChannel/TelegramChannel), so there
// is exactly one send implementation. `sendTest` is retained only for the
// interface/back-compat and is not wired to any UI button.

import { appendNotificationLog } from "./repositories";
import type {
  AlertEvent,
  AlertRule,
  DeliveryChannel,
  MessageTemplate,
  NotificationChannelResult,
  NotificationLog,
} from "./types";

export type EvaluatePreviewResult = {
  triggered: boolean;
  value?: number | string;
  detail?: string;
};

export interface AlertProvider {
  evaluatePreview(rule: AlertRule): Promise<EvaluatePreviewResult>;
  // `channelResults`, when provided, records the ACTUAL per-channel outcome
  // (e.g. a real device push that was verified to display). When omitted the
  // channels default to `sent` for preview-only callers.
  sendTest(
    uid: string,
    rule: AlertRule,
    channels: DeliveryChannel[],
    channelResults?: NotificationChannelResult[],
  ): Promise<NotificationLog>;
}

// Variables available to message templates. Kept loose so callers can pass any
// derived values; renderMessage only substitutes known keys.
export type MessageVars = {
  ticker?: string | number;
  value?: string | number;
  threshold?: string | number;
  name?: string | number;
  [key: string]: string | number | undefined;
};

// Simple {key} interpolation. Unknown placeholders are left untouched.
export function renderMessage(rule: AlertRule, vars: MessageVars = {}): MessageTemplate {
  const template: MessageTemplate = rule.delivery.message ?? { title: rule.name, body: rule.name };
  const merged: MessageVars = { name: rule.name, ...vars };

  const substitute = (text: string): string =>
    text.replace(/\{(\w+)\}/g, (match, key: string) => {
      const replacement = merged[key];
      return replacement === undefined ? match : String(replacement);
    });

  return {
    title: substitute(template.title),
    body: substitute(template.body),
  };
}

function extractTickers(rule: AlertRule): string[] {
  const condition = rule.condition;
  const tickers: string[] = [];
  switch (condition.kind) {
    case "ratio":
      tickers.push(condition.numerator, condition.denominator);
      break;
    case "dividend":
      tickers.push(condition.ticker);
      break;
    case "rsi":
    case "price":
      if ("ticker" in condition.metric) tickers.push(condition.metric.ticker);
      break;
    case "koreanEtf":
      if ("code" in condition.metric) tickers.push(condition.metric.code);
      break;
    default:
      break;
  }
  // Uppercased so the `tickers` array-contains search index (REQ-024) matches
  // regardless of how the ticker was typed when the rule was authored.
  return tickers.filter((value) => Boolean(value && value.trim())).map((value) => value.trim().toUpperCase());
}

export class MockAlertProvider implements AlertProvider {
  // Deterministic in-app preview: never triggered, with a sample value so the
  // UI can render a realistic preview without invoking the engine. Live
  // evaluation/gating/delivery is the Python engine's job (alert_engine/).
  async evaluatePreview(rule: AlertRule): Promise<EvaluatePreviewResult> {
    const sampleValue =
      rule.condition.kind === "ratio" || "threshold" in rule.condition
        ? 0
        : undefined;
    return {
      triggered: false,
      value: sampleValue,
      detail: "in-app preview — live evaluation runs in the engine",
    };
  }

  // sendTest builds an AlertEvent, renders the message, and writes a test
  // NotificationLog (isTest:true, channels marked sent). It intentionally does
  // NOT touch rule.lastTriggeredAt / rule.enabled — a test is a no-op on rule state.
  async sendTest(
    uid: string,
    rule: AlertRule,
    channels: DeliveryChannel[],
    channelResults?: NotificationChannelResult[],
  ): Promise<NotificationLog> {
    const now = new Date().toISOString();
    const eventId = `${rule.id}:test:${Date.now()}`;
    const tickers = extractTickers(rule);
    const message = renderMessage(rule, {
      ticker: tickers[0],
      value: typeof rule.lastValue === "number" || typeof rule.lastValue === "string" ? rule.lastValue : undefined,
      threshold: "threshold" in rule.condition ? rule.condition.threshold : undefined,
    });

    const event: AlertEvent = {
      eventId,
      ruleId: rule.id,
      uid,
      kind: rule.kind,
      message,
      evaluatedAt: now,
      sentAt: now,
      firedAt: now,
    };

    // Use the caller-supplied real outcomes when present; otherwise fall back to
    // `sent` for each requested channel (preview-only callers).
    const resolvedChannels: NotificationChannelResult[] =
      channelResults ?? channels.map((channel) => ({ channel, status: "sent" as const }));
    const anySent = resolvedChannels.some((c) => c.status === "sent");

    const log: NotificationLog = {
      id: eventId,
      eventId: event.eventId,
      ruleId: rule.id,
      kind: rule.kind,
      firedAt: event.firedAt,
      evaluatedAt: event.evaluatedAt,
      // Only stamp a delivery time when at least one channel actually sent.
      sentAt: anySent ? event.sentAt : undefined,
      message,
      channels: resolvedChannels,
      isTest: true,
      ruleName: rule.name,
      ...(tickers.length ? { tickers } : {}),
    };

    await appendNotificationLog(uid, log);
    return log;
  }
}

// The single seam: everything imports `alertProvider`, never MockAlertProvider directly.
export const alertProvider: AlertProvider = new MockAlertProvider();
