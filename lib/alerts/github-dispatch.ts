export const workflowTarget = () => ({
  repo: process.env.GITHUB_REPO || "cococute88/goralert",
  workflow: process.env.GITHUB_WORKFLOW_FILE || "alert-test-push.yml",
  ref: process.env.GITHUB_WORKFLOW_REF || "main",
});

export function githubHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}`, Accept: "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "goralert", "Content-Type": "application/json" };
}

export function dispatchFailure(status: number, state?: string) {
  if (state?.startsWith("disabled")) return { code: state, error: "알림 워크플로가 비활성화되어 테스트를 시작할 수 없습니다. 관리자 복구가 필요하며 예약 재시도도 실행되지 않습니다." };
  if (status === 401 || status === 403) return { code: "github_auth", error: "GitHub 실행 권한을 확인할 수 없습니다. 관리자에게 문의해 주세요." };
  if (status === 422) return { code: "dispatch_rejected", error: "GitHub가 실행 요청을 거절했습니다. 워크플로 상태와 설정 확인이 필요합니다." };
  return { code: "dispatch_unavailable", error: "워크플로 시작을 확인하지 못했습니다. 기록에서 요청 상태를 확인해 주세요." };
}
