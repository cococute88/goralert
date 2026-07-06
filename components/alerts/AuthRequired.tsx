"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// Shared loading + no-uid guards. Root AuthGate already enforces login, but each
// data-reading client page handles loading/!user gracefully as a safety net.

import { Loader2 } from "lucide-react";
import { EmptyState } from "./ui";

export function LoadingState({ label = "불러오는 중…" }: { label?: string }) {
  return (
    <div className="flex items-center justify-center gap-2 py-16 text-sm text-muted-foreground">
      <Loader2 size={18} className="animate-spin" />
      {label}
    </div>
  );
}

export function NoUserState() {
  return (
    <EmptyState
      title="로그인이 필요합니다"
      description="고라알림을 사용하려면 먼저 로그인해주세요."
    />
  );
}
