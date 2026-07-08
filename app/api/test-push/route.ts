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

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const DEFAULT_REPO = "cococute88/goralert";
const DEFAULT_WORKFLOW = "alert-test-push.yml";
const DEFAULT_REF = "main";

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
  const repo = process.env.GITHUB_REPO || DEFAULT_REPO;
  const workflow = process.env.GITHUB_WORKFLOW_FILE || DEFAULT_WORKFLOW;
  const ref = process.env.GITHUB_WORKFLOW_REF || DEFAULT_REF;

  // Fire workflow_dispatch. GitHub returns 204 on success; the run then drains
  // ONLY this uid's testPushRequests (workflow passes --uid).
  try {
    const res = await fetch(
      `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "goralert-test-push",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref, inputs: { uid } }),
      },
    );
    if (res.status === 204) {
      return NextResponse.json({ ok: true }, { status: 202 });
    }
    // Surface the real GitHub failure to the user (req 6).
    const detail = await res.text().catch(() => "");
    return NextResponse.json(
      { ok: false, error: `GitHub Actions 트리거 실패 (HTTP ${res.status}) ${detail}`.trim() },
      { status: 502 },
    );
  } catch (err) {
    return NextResponse.json(
      { ok: false, error: err instanceof Error ? err.message : "워크플로우 트리거 중 오류가 발생했습니다." },
      { status: 502 },
    );
  }
}
