"use client";

import { useFirebaseAuth } from "@/lib/firebase/auth";
import LandingLogin from "./LandingLogin";

// 앱 진입 게이트.
// - Firebase 미설정(로컬/미리보기): 기존 동작 그대로 통과시켜 서비스 진입.
// - 인증 복원 중(loading): 중립 스플래시를 보여 로그인 사용자가 랜딩을
//   잠깐이라도 보지 않도록 한다(자동 로그인 유지 정책 유지).
// - 인증된 사용자: 기존 서비스 화면으로 즉시 진입.
// - 비로그인 사용자: 랜딩 로그인 화면 표시.
export default function AuthGate({ children }: { children: React.ReactNode }) {
  const { user, loading, configured } = useFirebaseAuth();

  if (!configured) return <>{children}</>;
  if (loading) return <AuthSplash />;
  if (!user) return <LandingLogin />;
  return <>{children}</>;
}

// 세션 복원 동안의 중립 로딩 화면. 앱 기본 테마(다크)에 맞춘다.
function AuthSplash() {
  return (
    <div className="flex min-h-screen w-full items-center justify-center bg-[#111516] text-slate-300">
      <div
        className="h-6 w-6 animate-spin rounded-full border-2 border-slate-600 border-t-slate-200"
        role="status"
        aria-label="로그인 상태 확인 중"
      />
    </div>
  );
}
