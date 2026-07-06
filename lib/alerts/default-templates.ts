// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// Built-in (📦 기본) alert templates implementing the MVP scenarios (US-001..US-008)
// as ready-to-clone presets. These are NOT persisted — the UI merges them in and
// users clone them into their own ⭐ 즐겨찾기 via cloneRuleToDraft.

import type { AlertTemplate } from "./types";

const SEOUL_TZ = "Asia/Seoul";

const TELEGRAM_AND_PUSH = ["telegram", "push"] as const;

export const DEFAULT_TEMPLATES: AlertTemplate[] = [
  // US-001: VR 주문 — 격주 토요일 08:00
  {
    id: "builtin:vr-order",
    name: "VR 주문",
    kind: "date",
    isBuiltIn: true,
    condition: { kind: "date" },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "biweekly", weekday: 6, time: "08:00", tz: SEOUL_TZ },
    },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "VR 주문", body: "VR 주문을 넣으세요" },
    },
  },
  // US-002: 한국투자증권 이체 — 매월 말일 14:00
  {
    id: "builtin:kis-transfer",
    name: "한국투자증권 이체",
    kind: "date",
    isBuiltIn: true,
    condition: { kind: "date" },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "monthlyLastDay", time: "14:00", tz: SEOUL_TZ },
    },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "한국투자증권 이체", body: "한국투자증권으로 자금을 이체하세요" },
    },
  },
  // US-003: 미래에셋 예약매도 — 매월 1일
  {
    id: "builtin:miraeasset-presell",
    name: "미래에셋 예약매도",
    kind: "date",
    isBuiltIn: true,
    condition: { kind: "date" },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "monthlyFirstDay", tz: SEOUL_TZ },
    },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "미래에셋 예약매도", body: "미래에셋 예약매도 — 첫 22주 / 나머지 21주 시장가" },
    },
  },
  // US-004: SPY→SCHD 전환 — SPY/SCHD 비율 >= 25
  {
    id: "builtin:spy-schd-switch",
    name: "SPY→SCHD 전환",
    kind: "ratio",
    isBuiltIn: true,
    condition: { kind: "ratio", numerator: "SPY", denominator: "SCHD", comparator: "gte", threshold: 25 },
    trigger: { mode: "recurring" },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "SPY→SCHD 전환", body: "SPY→SCHD 전환을 고려해보세요" },
    },
  },
  // US-005: MSFT→SCHD 전환 — MSFT/SCHD 비율 >= 18
  {
    id: "builtin:msft-schd-switch",
    name: "MSFT→SCHD 전환",
    kind: "ratio",
    isBuiltIn: true,
    condition: { kind: "ratio", numerator: "MSFT", denominator: "SCHD", comparator: "gte", threshold: 18 },
    trigger: { mode: "recurring" },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "MSFT→SCHD 전환", body: "MSFT→SCHD 전환을 고려해보세요" },
    },
  },
  // US-006: 매수 마감 알림 — 별/하트 표시 종목, 18:00
  {
    id: "builtin:buy-deadline",
    name: "매수 마감 알림",
    kind: "date",
    isBuiltIn: true,
    condition: {
      kind: "date",
      selector: { source: "calendarEvents", markFilter: ["star", "heart"] },
    },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "calendar", time: "18:00", tz: SEOUL_TZ },
    },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "매수 마감 알림", body: "{ticker} 매수 마감일입니다" },
    },
  },
  // US-007: 배당락 알림 — 별/하트 표시 종목, 14:00
  {
    id: "builtin:ex-dividend",
    name: "배당락 알림",
    kind: "date",
    isBuiltIn: true,
    condition: {
      kind: "date",
      selector: { source: "calendarEvents", markFilter: ["star", "heart"] },
    },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "calendar", time: "14:00", tz: SEOUL_TZ },
    },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "배당락 알림", body: "{ticker} 배당락일입니다. 매도 주문을 넣으세요" },
    },
  },
  // US-008: KOSPI RSI 과매도 — RSI <= 50, 종가 확인 15:35
  {
    id: "builtin:kospi-rsi",
    name: "KOSPI RSI",
    kind: "rsi",
    isBuiltIn: true,
    condition: {
      kind: "rsi",
      metric: { metric: "rsi", ticker: "KOSPI", period: 14 },
      comparator: "lte",
      threshold: 50,
    },
    trigger: {
      mode: "recurring",
      recurrence: { kind: "calendar", time: "15:35", tz: SEOUL_TZ },
    },
    delivery: {
      channels: [...TELEGRAM_AND_PUSH],
      message: { title: "KOSPI RSI", body: "KOSPI RSI 과매도 — 종가 확인" },
    },
  },
];
