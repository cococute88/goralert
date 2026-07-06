"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// Shared low-level labeled inputs used by the per-kind alert forms. Token-based
// styling so light/dark both look correct.

import type { ReactNode, SelectHTMLAttributes, InputHTMLAttributes, TextareaHTMLAttributes } from "react";
import type { AlertRule } from "@/lib/alerts/types";
import { cx } from "../ui";

// Shared props contract for every per-kind form + common trailing field.
// `value` is the working draft; `onChange` receives the full merged draft.
export type RuleFormProps = {
  value: Partial<AlertRule>;
  onChange: (next: Partial<AlertRule>) => void;
};

export function Field({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: ReactNode;
  children: ReactNode;
}) {
  return (
    <label className="block space-y-1">
      <span className="text-xs font-medium text-foreground">{label}</span>
      {children}
      {hint ? <span className="block text-[11px] text-muted-foreground">{hint}</span> : null}
    </label>
  );
}

const CONTROL_CLASS =
  "w-full rounded-xl border border-border bg-background px-3 py-2 text-sm text-foreground outline-none focus:border-accent";

export function TextInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input className={cx(CONTROL_CLASS, className)} {...rest} />;
}

export function NumberInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="number" inputMode="decimal" className={cx(CONTROL_CLASS, className)} {...rest} />;
}

export function TimeInput({ className, ...rest }: InputHTMLAttributes<HTMLInputElement>) {
  return <input type="time" className={cx(CONTROL_CLASS, className)} {...rest} />;
}

export function TextArea({ className, ...rest }: TextareaHTMLAttributes<HTMLTextAreaElement>) {
  return <textarea className={cx(CONTROL_CLASS, "min-h-[80px] resize-y", className)} {...rest} />;
}

export function Select({ className, children, ...rest }: SelectHTMLAttributes<HTMLSelectElement>) {
  return (
    <select className={cx(CONTROL_CLASS, "appearance-none", className)} {...rest}>
      {children}
    </select>
  );
}

export const COMPARATOR_OPTIONS: { value: string; label: string }[] = [
  { value: "gte", label: "이상 (≥)" },
  { value: "gt", label: "초과 (>)" },
  { value: "lte", label: "이하 (≤)" },
  { value: "lt", label: "미만 (<)" },
  { value: "eq", label: "같음 (=)" },
  { value: "crossUp", label: "상향 돌파" },
  { value: "crossDown", label: "하향 돌파" },
];

export const SEOUL_TZ = "Asia/Seoul";
