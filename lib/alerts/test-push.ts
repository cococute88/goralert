// GORALERT — client helper that triggers the test-push workflow immediately.
//
// This calls the thin server bridge (app/api/test-push) which fires a GitHub
// Actions workflow_dispatch so the Python Alert Engine drains the caller's
// testPushRequests right away (instead of waiting for the 5-min cron). It does
// NOT deliver anything itself — delivery is done by the engine's PushChannel.

import type { User } from "firebase/auth";

export type DispatchResult = { ok: boolean; error?: string };

// Trigger an immediate engine run for this user. The request document must
// already be enqueued (repositories.enqueueTestPushRequest) before calling this.
export async function dispatchTestPushWorkflow(user: User): Promise<DispatchResult> {
  try {
    const idToken = await user.getIdToken();
    const res = await fetch("/api/test-push", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${idToken}`,
      },
      body: "{}",
    });
    const data = (await res.json().catch(() => ({}))) as DispatchResult;
    if (!res.ok || !data.ok) {
      return { ok: false, error: data.error ?? `트리거 실패 (HTTP ${res.status})` };
    }
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : "워크플로우 트리거에 실패했습니다." };
  }
}
