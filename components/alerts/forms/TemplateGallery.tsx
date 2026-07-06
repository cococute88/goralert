"use client";

// GORALERT-ALERT-SYSTEM Layer B1 (REQ-046)
// Template gallery: 📦 기본 템플릿(DEFAULT_TEMPLATES) + ⭐ 내 즐겨찾기(loadAlertTemplates).
// 각 템플릿은 종류 + 일정/조건 요약(preview)을 보여주고, 선택 시 cloneRuleToDraft 로
// 새 draft 를 만들어 폼을 prefill 한다. 내 즐겨찾기는 삭제(deleteAlertTemplate)할 수 있다.

import { useEffect, useState } from "react";
import { Loader2, Trash2 } from "lucide-react";
import type { AlertRule, AlertTemplate, Comparator, Recurrence, TriggerPolicy } from "@/lib/alerts/types";
import { DEFAULT_TEMPLATES } from "@/lib/alerts/default-templates";
import { deleteAlertTemplate, loadAlertTemplates } from "@/lib/alerts/repositories";
import { cloneRuleToDraft } from "@/lib/alerts/clone";
import { alertKindLabel } from "./AlertKindBadge";
import { Badge, ConfirmDialog } from "../ui";
import { useToast } from "../ui/toast";

const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

const COMPARATOR_LABELS: Record<Comparator, string> = {
  gt: ">",
  gte: "≥",
  lt: "<",
  lte: "≤",
  eq: "=",
  crossUp: "↑ 상향 돌파",
  crossDown: "↓ 하향 돌파",
};

// 일정(트리거) 요약 — "격주 토 08:00" / "매월 말일 14:00" / "캘린더 기준 18:00" 등.
function describeSchedule(trigger: TriggerPolicy): string {
  if (trigger.mode === "once") return "1회 발송";
  const recurrence: Recurrence | undefined = trigger.recurrence;
  if (!recurrence) return "반복";
  const time = recurrence.time ? ` ${recurrence.time}` : "";
  const weekday = recurrence.weekday !== undefined ? WEEKDAYS[recurrence.weekday] ?? "" : "";
  switch (recurrence.kind) {
    case "weekly":
      return `매주 ${weekday}요일${time}`;
    case "biweekly":
      return `격주 ${weekday}요일${time}`;
    case "monthlyFirstDay":
      return `매월 1일${time}`;
    case "monthlyLastDay":
      return `매월 말일${time}`;
    case "calendar":
      return `캘린더 일정 기준${time}`;
    default:
      return "반복";
  }
}

// 조건 요약 — ratio/metric 임계값을 사람이 읽기 쉬운 형태로.
function describeCondition(template: AlertTemplate): string | null {
  const condition = template.condition;
  switch (condition.kind) {
    case "ratio":
      return `${condition.numerator}/${condition.denominator} ${COMPARATOR_LABELS[condition.comparator]} ${condition.threshold}`;
    case "rsi":
    case "vix":
    case "price":
    case "fx":
    case "gold":
    case "bitcoin":
    case "koreanEtf": {
      const metric = condition.metric;
      const label =
        "ticker" in metric
          ? metric.ticker
          : "code" in metric
            ? metric.code
            : "pair" in metric
              ? metric.pair
              : alertKindLabel(condition.kind);
      return `${label} ${COMPARATOR_LABELS[condition.comparator]} ${condition.threshold}`;
    }
    case "date":
      return condition.selector?.markFilter?.length ? "⭐/❤️ 표시 종목" : null;
    default:
      return null;
  }
}

function TemplateCard({
  template,
  onPick,
  onDelete,
  deleting,
}: {
  template: AlertTemplate;
  onPick: (draft: Partial<AlertRule>) => void;
  onDelete?: () => void;
  deleting?: boolean;
}) {
  const schedule = describeSchedule(template.trigger);
  const condition = describeCondition(template);
  return (
    <div className="flex items-stretch gap-2">
      <button
        type="button"
        onClick={() => onPick(cloneRuleToDraft(template))}
        className="flex min-w-0 flex-1 flex-col gap-1.5 rounded-xl border border-border bg-card px-4 py-3 text-left transition-colors hover:border-accent"
      >
        <span className="flex items-center justify-between gap-2">
          <span className="truncate text-sm font-medium text-foreground">{template.name}</span>
          <Badge tone="accent">{alertKindLabel(template.kind)}</Badge>
        </span>
        {template.delivery.message?.body ? (
          <span className="truncate text-xs text-muted-foreground">{template.delivery.message.body}</span>
        ) : null}
        <span className="flex flex-wrap gap-1.5">
          <Badge tone="neutral">{schedule}</Badge>
          {condition ? <Badge tone="neutral">{condition}</Badge> : null}
        </span>
      </button>
      {onDelete ? (
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          aria-label={`${template.name} 즐겨찾기 삭제`}
          title="즐겨찾기에서 삭제"
          className="flex w-10 shrink-0 items-center justify-center rounded-xl border border-border bg-card text-muted-foreground transition-colors hover:border-danger hover:text-danger disabled:opacity-50"
        >
          {deleting ? <Loader2 size={16} className="animate-spin" /> : <Trash2 size={16} />}
        </button>
      ) : null}
    </div>
  );
}

export default function TemplateGallery({
  uid,
  onPick,
}: {
  uid: string;
  onPick: (draft: Partial<AlertRule>) => void;
}) {
  const toast = useToast();
  const [favorites, setFavorites] = useState<AlertTemplate[]>([]);
  const [loading, setLoading] = useState(true);
  const [deletingId, setDeletingId] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<AlertTemplate | null>(null);

  useEffect(() => {
    let active = true;
    loadAlertTemplates(uid)
      .then((rows) => {
        if (active) setFavorites(rows);
      })
      .catch(() => {
        if (active) setFavorites([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [uid]);

  const handleDelete = async (template: AlertTemplate) => {
    setPendingDelete(null);
    setDeletingId(template.id);
    // 낙관적 제거.
    const prev = favorites;
    setFavorites((rows) => rows.filter((row) => row.id !== template.id));
    try {
      await deleteAlertTemplate(uid, template.id);
      toast.success("즐겨찾기를 삭제했어요");
    } catch (err) {
      setFavorites(prev); // 롤백
      toast.error(err instanceof Error ? err.message : "삭제에 실패했습니다");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">📦 기본 템플릿</h2>
          <p className="text-xs text-muted-foreground">자주 쓰는 시나리오를 바로 복제해 시작할 수 있어요.</p>
        </div>
        <div className="space-y-2">
          {DEFAULT_TEMPLATES.map((template) => (
            <TemplateCard key={template.id} template={template} onPick={onPick} />
          ))}
        </div>
      </section>

      <section className="space-y-2">
        <div>
          <h2 className="text-sm font-semibold text-foreground">⭐ 내 즐겨찾기</h2>
          <p className="text-xs text-muted-foreground">알림을 만들 때 “즐겨찾기로 저장”한 내 템플릿이에요.</p>
        </div>
        {loading ? (
          <div className="flex items-center gap-2 py-4 text-xs text-muted-foreground">
            <Loader2 size={14} className="animate-spin" />
            불러오는 중…
          </div>
        ) : favorites.length === 0 ? (
          <p className="rounded-xl border border-dashed border-border px-4 py-6 text-center text-xs text-muted-foreground">
            저장된 즐겨찾기가 없습니다. 알림을 만들 때 “즐겨찾기로 저장”을 눌러보세요.
          </p>
        ) : (
          <div className="space-y-2">
            {favorites.map((template) => (
              <TemplateCard
                key={template.id}
                template={template}
                onPick={onPick}
                onDelete={() => setPendingDelete(template)}
                deleting={deletingId === template.id}
              />
            ))}
          </div>
        )}
      </section>

      <ConfirmDialog
        open={pendingDelete !== null}
        title="즐겨찾기를 삭제할까요?"
        description={
          pendingDelete ? (
            <>
              <span className="font-medium text-foreground">{pendingDelete.name}</span> 즐겨찾기가 삭제됩니다.
              이미 만든 알림에는 영향을 주지 않아요.
            </>
          ) : undefined
        }
        confirmLabel="삭제"
        busy={deletingId !== null}
        onConfirm={() => {
          if (pendingDelete) void handleDelete(pendingDelete);
        }}
        onCancel={() => setPendingDelete(null)}
      />
    </div>
  );
}
