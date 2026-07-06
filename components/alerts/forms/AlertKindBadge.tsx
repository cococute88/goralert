"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// Korean labels for AlertKind + a small badge used in lists/cards.

import type { AlertKind } from "@/lib/alerts/types";
import { Badge } from "../ui";

export const ALERT_KIND_LABELS: Record<AlertKind, string> = {
  date: "날짜",
  ratio: "전환비",
  dividend: "배당",
  rsi: "RSI",
  vix: "VIX",
  price: "가격",
  fx: "환율",
  gold: "금",
  bitcoin: "비트코인",
  koreanEtf: "국내 ETF",
  custom: "사용자 정의",
  composite: "복합",
};

export function alertKindLabel(kind: AlertKind): string {
  return ALERT_KIND_LABELS[kind] ?? kind;
}

export default function AlertKindBadge({ kind }: { kind: AlertKind }) {
  return <Badge tone="accent">{alertKindLabel(kind)}</Badge>;
}
