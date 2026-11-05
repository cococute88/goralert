// GORALERT — thin bridge: browser -> GitHub Actions workflow_dispatch.
//
// This route contains NO push implementation. It only (1) verifies the caller's
// Firebase ID token and (2) triggers the alert-test-push workflow immediately so
// the Python Alert Engine drains that user's testPushRequests right away instead
// of waiting for the 5-minute cron. Actual delivery is done exclusively by the
// engine's PushChannel (the single source of truth). The request document itself
// is created client-side (repositories.enqueueTestPushRequest) before this call.
//
// Required server env (never exposed to the browser):
//   GITHUB_DISPATCH_TOKEN  PAT / fine-grained token with actions:write on the repo
//   GITHUB_REPO            "owner/repo" (default: cococute88/goralert)
//   GITHUB_WORKFLOW_FILE   workflow filename (default: alert-test-push.yml)
//   GITHUB_WORKFLOW_REF    git ref to run on (default: main)
// Reused public env:
//   NEXT_PUBLIC_FIREBASE_API_KEY  used only to verify the ID token belongs here

import { NextResponse } from "next/server";
import { dispatchFailure, githubHeaders, workflowTarget } from "@/lib/alerts/github-dispatch";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";


// Verify a Firebase ID token via Google's Identity Toolkit (no admin SDK / no new
// dependency). Returns the uid when the token is valid & unexpired for THIS
// Firebase project, otherwise null.
async function verifyFirebaseToken(idToken: string): Promise<string | null> {
  const apiKey = process.env.NEXT_PUBLIC_FIREBASE_API_KEY;
  if (!apiKey) return null;
  try {
    const res = await fetch(
      `https://identitytoolkit.googleapis.com/v1/accounts:lookup?key=${apiKey}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ idToken }),
        signal: AbortSignal.timeout(8000),
      },
    );
    if (!res.ok) return null;
    const data = (await res.json()) as { users?: Array<{ localId?: string }> };
    return data.users?.[0]?.localId ?? null;
  } catch {
    return null;
  }
}

export async function POST(request: Request): Promise<Response> {
  const auth = request.headers.get("authorization") ?? "";
  const idToken = auth.toLowerCase().startsWith("bearer ") ? auth.slice(7).trim() : "";
  if (!idToken) {
    return NextResponse.json({ ok: false, error: "인증 토큰이 없습니다." }, { status: 401 });
  }

  const uid = await verifyFirebaseToken(idToken);
  if (!uid) {
    return NextResponse.json({ ok: false, error: "인증에 실패했습니다. 다시 로그인해 주세요." }, { status: 401 });
  }

  const token = process.env.GITHUB_DISPATCH_TOKEN;
  if (!token) {
    return NextResponse.json(
      { ok: false, error: "서버에 GITHUB_DISPATCH_TOKEN이 설정되지 않았습니다. (관리자 설정 필요)" },
      { status: 500 },
    );
  }
  const { repo, workflow, ref } = workflowTarget();
  const headers = githubHeaders(token);

  // Fire workflow_dispatch. GitHub returns 204 on success; the run then drains
  // ONLY this uid's testPushRequests (workflow passes --uid).
  try {
    const stateResponse = await fetch(`https://api.github.com/repos/${repo}/actions/workflows/${encodeURIComponent(workflow)}`, {
      headers, cache: "no-store", signal: AbortSignal.timeout(8000),
    });
    if (!stateResponse.ok) return NextResponse.json({ ok: false, ...dispatchFailure(stateResponse.status), detail: `Workflow status HTTP ${stateResponse.status}`, fallbackAvailable: false }, { status: 502 });
    const state = String((await stateResponse.json()).state);
    if (state !== "active") return NextResponse.json({ ok: false, ...dispatchFailure(422, state), detail: `Workflow state: ${state}`, fallbackAvailable: false }, { status: 409 });
    const res = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ ref, inputs: { uid } }),
        signal: AbortSignal.timeout(8000),
      },
    );
    if (res.status === 204) {
      return NextResponse.json({ ok: true, accepted: true, fallbackAvailable: false }, { status: 202 });
    }
    // Surface the real GitHub failure to the user (req 6).
    return NextResponse.json(
      { ok: false, ...dispatchFailure(res.status), detail: `Dispatch HTTP ${res.status}`, fallbackAvailable: false },
      { status: 502 },
    );
  } catch {
    return NextResponse.json(
      { ok: false, error: "워크플로 시작 상태를 확인할 수 없습니다. 기록에서 요청 상태를 확인해 주세요.", fallbackAvailable: false },
      { status: 502 },
    );
  }
}
