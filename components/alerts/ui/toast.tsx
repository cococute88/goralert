"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// Minimal toast system scoped to the Goralert shell. A ToastProvider holds the
// queue; useToast() exposes push helpers. Used for 테스트 발송 / 저장 결과 등.

import { createContext, useCallback, useContext, useMemo, useState } from "react";
import type { ReactNode } from "react";
import { CheckCircle2, Info, XCircle } from "lucide-react";
import { generateId } from "@/lib/alerts/id";

type ToastTone = "success" | "error" | "info";

type ToastItem = {
  id: string;
  tone: ToastTone;
  message: string;
};

type ToastContextValue = {
  show: (message: string, tone?: ToastTone) => void;
  success: (message: string) => void;
  error: (message: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);

  const remove = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id));
  }, []);

  const show = useCallback(
    (message: string, tone: ToastTone = "info") => {
      const id = generateId();
      setToasts((prev) => [...prev, { id, tone, message }]);
      setTimeout(() => remove(id), 3200);
    },
    [remove],
  );

  const value = useMemo<ToastContextValue>(
    () => ({
      show,
      success: (message: string) => show(message, "success"),
      error: (message: string) => show(message, "error"),
    }),
    [show],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div className="pointer-events-none fixed inset-x-0 bottom-24 z-50 flex flex-col items-center gap-2 px-4">
        {toasts.map((toast) => (
          <ToastCard key={toast.id} toast={toast} onClose={() => remove(toast.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

function ToastCard({ toast, onClose }: { toast: ToastItem; onClose: () => void }) {
  const Icon = toast.tone === "success" ? CheckCircle2 : toast.tone === "error" ? XCircle : Info;
  const toneClass =
    toast.tone === "success" ? "text-success" : toast.tone === "error" ? "text-danger" : "text-accent";
  return (
    <button
      type="button"
      onClick={onClose}
      className="pointer-events-auto flex w-full max-w-sm items-center gap-2 rounded-xl border border-border bg-card px-4 py-3 text-sm text-card-foreground shadow-lg"
    >
      <Icon size={18} className={toneClass} />
      <span className="text-left">{toast.message}</span>
    </button>
  );
}

export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    // Fallback no-op so components don't crash outside the provider.
    return { show: () => {}, success: () => {}, error: () => {} };
  }
  return ctx;
}
