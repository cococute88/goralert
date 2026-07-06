"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-042)
// 알림 목록. 각 규칙을 카드로 보여주고 enable 토글/테스트/편집/삭제/복제를 제공한다.

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { Copy, Loader2, Pencil, Plus, Send, Star, Trash2 } from "lucide-react";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import type { AlertRule } from "@/lib/alerts/types";
import { deleteAlertRule, loadAlertRules, setAlertRuleEnabled } from "@/lib/alerts/repositories";
import { alertProvider } from "@/lib/alerts/provider";
import { cloneRuleToDraft } from "@/lib/alerts/clone";
import { formatNextOccurrence, nextOccurrence } from "@/lib/alerts/schedule";
import { Badge, Button, Card, CardSection, ConfirmDialog, EmptyState, Toggle } from "@/components/alerts/ui";
import { useToast } from "@/components/alerts/ui/toast";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import AlertKindBadge from "@/components/alerts/forms/AlertKindBadge";
import { stashDraft } from "@/components/alerts/draftStore";

function RuleCard({
  rule,
  uid,
  onChanged,
}: {
  rule: AlertRule;
  uid: string;
  onChanged: () => void;
}) {
  const router = useRouter();
  const toast = useToast();
  const [enabled, setEnabled] = useState(rule.enabled);
  const [testing, setTesting] = useState(false);
  const [busy, setBusy] = useState(false);
  const [confirmOpen, setConfirmOpen] = useState(false);

  const next = nextOccurrence(rule.trigger);

  const handleToggle = async (value: boolean) => {
    setEnabled(value);
    try {
      await setAlertRuleEnabled(uid, rule.id, value);
    } catch (err) {
      setEnabled(!value);
      toast.error(err instanceof Error ? err.message : "상태 변경에 실패했습니다");
    }
  };

  const handleTest = async () => {
    setTesting(true);
    try {
      const log = await alertProvider.sendTest(uid, rule, rule.delivery.channels);
      const channels = log.channels.map((c) => c.channel).join(", ");
      toast.success(`테스트 발송 완료 (${channels || "채널 없음"})`);
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "테스트 발송에 실패했습니다");
    } finally {
      setTesting(false);
    }
  };

  const handleClone = () => {
    stashDraft(cloneRuleToDraft(rule));
    router.push("/alerts/new");
  };

  const handleDelete = async () => {
    setBusy(true);
    try {
      await deleteAlertRule(uid, rule.id);
      toast.success("알림을 삭제했습니다");
      onChanged();
    } catch (err) {
      toast.error(err instanceof Error ? err.message : "삭제에 실패했습니다");
      setBusy(false);
      setConfirmOpen(false);
    }
  };

  return (
    <Card>
      <CardSection className="space-y-3">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-foreground">{rule.name}</span>
              <AlertKindBadge kind={rule.kind} />
            </div>
            <p className="mt-1 text-xs text-muted-foreground">
              다음 예정: {next ? formatNextOccurrence(next) : "캘린더/이벤트 기반"}
            </p>
            <p className="text-[11px] text-muted-foreground">
              마지막 발송: {rule.lastTriggeredAt ? new Date(rule.lastTriggeredAt).toLocaleString("ko-KR") : "없음"}
            </p>
          </div>
          <Toggle checked={enabled} onChange={handleToggle} label={`${rule.name} 사용`} />
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" variant="secondary" onClick={handleTest} disabled={testing}>
            {testing ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
            테스트
          </Button>
          <Link href={`/alerts/${rule.id}`}>
            <Button size="sm" variant="secondary">
              <Pencil size={14} />
              편집
            </Button>
          </Link>
          <Button size="sm" variant="secondary" onClick={handleClone}>
            <Copy size={14} />
            복제
          </Button>
          <Button size="sm" variant="danger" onClick={() => setConfirmOpen(true)} disabled={busy}>
            <Trash2 size={14} />
            삭제
          </Button>
        </div>
      </CardSection>

      <ConfirmDialog
        open={confirmOpen}
        title="알림을 삭제할까요?"
        description={
          <>
            <span className="font-medium text-foreground">{rule.name}</span> 알림이 영구적으로 삭제됩니다. 이
            작업은 되돌릴 수 없어요.
          </>
        }
        confirmLabel="삭제"
        busy={busy}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmOpen(false)}
      />
    </Card>
  );
}

export default function AlertsListPage() {
  const { user, loading: authLoading } = useFirebaseAuth();
  const [rules, setRules] = useState<AlertRule[]>([]);
  const [loading, setLoading] = useState(true);
  const [reloadKey, setReloadKey] = useState(0);

  const refresh = () => setReloadKey((key) => key + 1);

  useEffect(() => {
    if (!user) return;
    let active = true;
    setLoading(true);
    loadAlertRules(user.uid)
      .then((rows) => {
        if (active) setRules(rows);
      })
      .catch(() => {
        if (active) setRules([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user, reloadKey]);

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-lg font-bold text-foreground">내 알림</h1>
        <Badge>{rules.length}</Badge>
      </div>

      <div className="flex gap-2">
        <Link href="/alerts/new" className="block flex-1">
          <Button className="w-full">
            <Plus size={16} />새 알림 만들기
          </Button>
        </Link>
        <Link href="/alerts/new?start=template" className="block">
          <Button variant="secondary" aria-label="즐겨찾기/템플릿에서 만들기">
            <Star size={16} />즐겨찾기
          </Button>
        </Link>
      </div>

      {loading ? (
        <LoadingState />
      ) : rules.length === 0 ? (
        <EmptyState
          title="아직 만든 알림이 없어요"
          description="“새 알림 만들기”로 첫 알림을 추가해보세요."
        />
      ) : (
        <div className="space-y-3">
          {rules.map((rule) => (
            <RuleCard key={rule.id} rule={rule} uid={user.uid} onChanged={refresh} />
          ))}
        </div>
      )}
    </div>
  );
}
