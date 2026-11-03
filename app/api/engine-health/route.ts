import { NextResponse } from "next/server";
import { classifyEngineHealth } from "@/lib/alerts/engine-health";
import { githubHeaders, workflowTarget } from "@/lib/alerts/github-dispatch";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
let cached: { at: number; value: ReturnType<typeof classifyEngineHealth> } | undefined;

export async function GET() {
  if (cached && Date.now() - cached.at < 60_000) return NextResponse.json(cached.value, { headers: { "Cache-Control": "no-store" } });
  let value: ReturnType<typeof classifyEngineHealth>;
  try {
    const token = process.env.GITHUB_DISPATCH_TOKEN;
    const project = process.env.NEXT_PUBLIC_FIREBASE_PROJECT_ID;
    if (!token || !project) throw new Error("health configuration unavailable");
    const { repo, workflow } = workflowTarget();
    const files = ["alert-engine.yml", workflow];
    const responses = await Promise.all([
      fetch(`https://firestore.googleapis.com/v1/projects/${encodeURIComponent(project)}/databases/(default)/documents/systemHealth/alertEngine`, { cache: "no-store", signal: AbortSignal.timeout(8000) }),
      ...files.map((file) => fetch(`https://api.github.com/repos/${repo}/actions/workflows/${encodeURIComponent(file)}`, { headers: githubHeaders(token), cache: "no-store", signal: AbortSignal.timeout(8000) })),
    ]);
    const workflows: Record<string, string> = {};
    for (let i = 0; i < files.length; i++) {
      const response = responses[i + 1];
      workflows[files[i]] = response.ok ? String((await response.json()).state) : "unknown";
    }
    let heartbeat: Record<string, unknown> | null = null;
    if (responses[0].ok) {
      const document = await responses[0].json();
      heartbeat = Object.fromEntries(Object.entries(document.fields ?? {}).map(([key, field]) => {
        const item = field as Record<string, unknown>;
        return [key, item.stringValue ?? item.timestampValue ?? item.integerValue];
      }));
    }
    value = classifyEngineHealth(heartbeat, workflows);
  } catch {
    value = { state: "unknown", message: "엔진 상태 조회에 실패했습니다. 정상 여부를 확인할 수 없습니다." };
  }
  cached = { at: Date.now(), value };
  return NextResponse.json(value, { headers: { "Cache-Control": "no-store" } });
}
