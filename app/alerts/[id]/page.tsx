"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-042)
// 기존 알림 편집. getAlertRule 로 불러와 동일한 RuleForm 으로 수정 후 saveAlertRule.

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import type { AlertRule } from "@/lib/alerts/types";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import { getAlertRule } from "@/lib/alerts/repositories";
import { LoadingState, NoUserState } from "@/components/alerts/AuthRequired";
import { EmptyState } from "@/components/alerts/ui";
import RuleForm from "@/components/alerts/forms/RuleForm";
import { deriveFormKind } from "@/components/alerts/forms/ruleModel";

export default function EditAlertPage() {
  const router = useRouter();
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params.id) ? params.id[0] : params.id;
  const { user, loading: authLoading } = useFirebaseAuth();
  const [draft, setDraft] = useState<Partial<AlertRule> | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!user || !id) return;
    let active = true;
    setLoading(true);
    getAlertRule(user.uid, id)
      .then((rule) => {
        if (!active) return;
        if (rule) setDraft(rule);
        else setNotFound(true);
      })
      .catch(() => {
        if (active) setNotFound(true);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [user, id]);

  if (authLoading) return <LoadingState />;
  if (!user) return <NoUserState />;
  if (loading) return <LoadingState />;
  if (notFound || !draft) {
    return (
      <EmptyState
        title="알림을 찾을 수 없어요"
        description="삭제되었거나 잘못된 주소일 수 있습니다."
      />
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => router.push("/alerts")}
          className="rounded-lg p-1 text-muted-foreground hover:text-foreground"
          aria-label="뒤로"
        >
          <ChevronLeft size={20} />
        </button>
        <h1 className="text-lg font-bold text-foreground">알림 편집</h1>
      </div>

      <RuleForm
        uid={user.uid}
        formKind={deriveFormKind(draft)}
        draft={draft}
        setDraft={(next) => setDraft(next)}
        submitLabel="변경 사항 저장"
        onSaved={() => router.push("/alerts")}
      />
    </div>
  );
}
