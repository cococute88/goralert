import type { CalendarEventStatus, CalendarEventType } from "./calendar-types";

type CalendarEvent = {
  id: string;
  date: string;
  ticker: string;
  type: CalendarEventType | string;
  title?: string;
  status: CalendarEventStatus;
};

export const EVENT_VISUALS: Record<CalendarEventType, { label: string; shortLabel: string; ko: string; bg: string; border: string; text: string }> = {
  ex_div: { label: "배당락", shortLabel: "배당락", ko: "배당락", bg: "bg-blue-500/20", border: "border-blue-400/60", text: "text-blue-200" },
  buy_by: { label: "매수마감", shortLabel: "매수", ko: "매수 마감", bg: "bg-red-500/20", border: "border-red-400/60", text: "text-red-200" },
  pay: { label: "지급", shortLabel: "지급", ko: "지급", bg: "bg-emerald-500/20", border: "border-emerald-400/60", text: "text-emerald-200" },
  earnings: { label: "실적", shortLabel: "실적", ko: "실적", bg: "bg-purple-500/20", border: "border-purple-400/60", text: "text-purple-200" },
  custom: { label: "사용자", shortLabel: "사용자", ko: "사용자 일정", bg: "bg-amber-500/15", border: "border-amber-300/50", text: "text-amber-100" },
};

export function getEventVisual(type: string): (typeof EVENT_VISUALS)[CalendarEventType] {
  return EVENT_VISUALS[type as CalendarEventType] ?? EVENT_VISUALS.custom;
}

export function eventChipLabel(event: Pick<CalendarEvent, "ticker" | "type" | "title">): string {
  if (event.type === "custom") return event.title ?? event.ticker;
  return `${event.ticker} ${getEventVisual(event.type).shortLabel}`;
}

export function eventStatusLabel(status: CalendarEventStatus): string {
  return status === "confirmed" ? "확정" : "추정";
}

// Selected-date card status label. The card shows a 확정/예상 binary (declared /
// confirmed → 확정, estimated / 추정 / 미확정 → 예상) alongside the date and the
// per-$10k tax-saving estimate, so it reads "2026-06-15 · $12 · 확정".
export function eventStatusShortLabel(status: CalendarEventStatus): string {
  return status === "confirmed" ? "확정" : "예상";
}

// Format a per-$10k tax-saving estimate (현시세 기준) for the selected-date card.
// Returns "—" when no computable value exists — never fabricate a number.
export function formatTaxSavingPer10k(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return "—";
  // "$12" (whole) or "$8.4" (one decimal) — drop a trailing ".0".
  return `$${value.toFixed(1).replace(/\.0$/, "")}`;
}

// Format a per-$10k tax-saving estimate for a monthly calendar chip, mirroring
// the right-rail 절세액 table's two-decimal `$17.25` rendering (same source
// value). Returns null when no computable value exists so the chip keeps its
// plain `CRBG 매수` label instead of fabricating an amount.
export function formatTaxSavingChipAmount(value: number | null | undefined): string | null {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) return null;
  return `$${value.toFixed(2)}`;
}

export function eventStateClasses(event: CalendarEvent, todayIso: string): string {
  const isPast = event.date < todayIso;
  const estimated = event.status === "estimated";
  // Opacity policy:
  //  - declared/confirmed & upcoming → full opacity
  //  - declared/confirmed & past     → light muted veil (~60%)
  //  - non-declared (estimated)      → subtle (~80%) so 확정 vs 추정 reads at a
  //    glance, but still legible. The dashed border is the primary 추정 cue.
  // Past events keep their full event-type color (no desaturation) so
  // Ex-Div/Buy/Pay/Earn stay distinguishable in light and dark mode. Estimated
  // events stay dashed.
  const opacity = estimated ? "opacity-80" : isPast ? "opacity-60" : "opacity-100";
  return [opacity, estimated ? "border-dashed" : "border-solid"].join(" ");
}
