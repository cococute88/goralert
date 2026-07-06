"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-046)
// Step 1 of the create flow: choose the alert 종류. Includes a 템플릿 entry that
// opens the template gallery (기본 템플릿 + 내 즐겨찾기).

import type { FormKind } from "./ruleModel";
import { FORM_KIND_META } from "./ruleModel";

const ORDER: FormKind[] = ["date", "ratio", "metric", "calendar", "custom"];

export type TypeChoice = FormKind | "template";

export default function AlertTypeSelector({ onSelect }: { onSelect: (choice: TypeChoice) => void }) {
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">어떤 알림을 만들까요?</p>
      <div className="grid grid-cols-1 gap-2">
        {ORDER.map((kind) => {
          const meta = FORM_KIND_META[kind];
          return (
            <button
              key={kind}
              type="button"
              onClick={() => onSelect(kind)}
              className="flex items-center gap-3 rounded-2xl border border-border bg-card p-4 text-left transition-colors hover:border-accent"
            >
              <span className="text-2xl" aria-hidden>
                {meta.emoji}
              </span>
              <span className="flex-1">
                <span className="block text-sm font-semibold text-foreground">{meta.label}</span>
                <span className="block text-xs text-muted-foreground">{meta.description}</span>
              </span>
            </button>
          );
        })}

        <button
          type="button"
          onClick={() => onSelect("template")}
          className="flex items-center gap-3 rounded-2xl border border-border bg-card p-4 text-left transition-colors hover:border-accent"
        >
          <span className="text-2xl" aria-hidden>
            📦
          </span>
          <span className="flex-1">
            <span className="block text-sm font-semibold text-foreground">템플릿에서 만들기</span>
            <span className="block text-xs text-muted-foreground">기본 템플릿 또는 내 즐겨찾기로 빠르게 시작</span>
          </span>
        </button>
      </div>
    </div>
  );
}
