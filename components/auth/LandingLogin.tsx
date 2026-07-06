"use client";

import Image from "next/image";
import { useFirebaseAuth } from "@/lib/firebase/auth";
import {
  getLandingHeroVariant,
  type LandingHeroVariant,
} from "@/lib/season";
// 정적 import 를 사용하면 Next 가 빌드 시 너비/높이와 blur 플레이스홀더를
// 자동 생성하고, 최적화된 WebP(약 200~300KB, q90/method6)를 인라인으로 연결해
// 로딩이 빨라진다. 마스터 WebP 는 scripts/generate-hero-webp.py 단일 파이프라인으로
// 생성하며, 히어로 이미지 7종(여름은 이른 여름/늦여름, 겨울은 12월/1월/2월로 세분)
// 모두 동일한 품질 기준(q90)으로 관리한다.
// (정적 import 는 URL/메타데이터만 제공하므로, 실제 네트워크 요청은
//  현재 시기로 렌더된 <Image> 1장에 대해서만 발생한다.)
import goraniSpring from "@/public/gorani_spring.webp";
import goraniSummer from "@/public/gorani_summer.webp";
import goraniBeach from "@/public/gorani_beach.webp";
import goraniFall from "@/public/gorani_fall.webp";
import goraniWinter from "@/public/gorani_winter.webp";
import goraniNewyear1 from "@/public/gorani_newyear_1.webp";
import goraniNewyear2 from "@/public/gorani_newyear_2.webp";

// 히어로 변형 -> 이미지 매핑.
//  - summer:   이른 여름(6/1~7/15)
//  - beach:    늦여름(7/16~8/31)
//  - winter:   12월
//  - newyear1: 1월 / newyear2: 2월
const HERO_IMAGES: Record<LandingHeroVariant, typeof goraniSpring> = {
  spring: goraniSpring,
  summer: goraniSummer,
  beach: goraniBeach,
  fall: goraniFall,
  winter: goraniWinter,
  newyear1: goraniNewyear1,
  newyear2: goraniNewyear2,
};

// 비로그인 사용자 전용 랜딩 로그인 화면.
// 좌측: 밝은 배경 + 브랜드명 + Welcome back + Google 로그인 버튼.
// 우측: 현재 계절에 맞는 고라니 히어로 이미지(비율 유지, object-cover).
// 로그인 로직은 기존 useFirebaseAuth().signInWithGoogle 를 그대로 사용한다.
export default function LandingLogin() {
  const { signInWithGoogle, loading, error, configured } = useFirebaseAuth();

  // 현재 날짜 기준 히어로 변형(계절 + 여름 세분)을 판별해 이미지를 선택한다.
  // 순수 함수라 SSR/클라이언트가 동일한 날짜에서 같은 결과를 내며,
  // 시기가 바뀌면 코드 수정 없이 자동으로 다른 이미지가 선택된다.
  const heroVariant = getLandingHeroVariant();
  const heroImage = HERO_IMAGES[heroVariant];

  return (
    <div className="flex min-h-screen w-full flex-col overflow-x-hidden bg-white text-slate-900 md:flex-row">
      {/* 우측 히어로 이미지 — 모바일에서는 상단 배너(뷰포트 높이의 약 40%로 키워
          캐릭터가 보이게 한다), 데스크톱에서는 우측 55~60% */}
      <div className="relative order-1 h-[40vh] w-full shrink-0 sm:h-[44vh] md:order-2 md:h-auto md:min-h-screen md:w-[57%] lg:w-[58%]">
        <Image
          src={heroImage}
          alt="고라니 브랜드 이미지"
          fill
          priority
          placeholder="blur"
          // Next 이미지 최적화기는 마스터 WebP 를 재인코딩하므로, 기본 q75 로
          // 한 번 더 압축되면 이미 손실 압축된 소스가 이중 압축돼 디테일이
          // 뭉개진다. 마스터 품질(q90)에 맞춰 90 으로 올려 이중 압축 손실을 없앤다.
          quality={90}
          sizes="(max-width: 768px) 100vw, 58vw"
          // 모바일은 캐릭터(이미지 하단 중앙)가 보이도록 아래쪽을 기준으로 크롭하고,
          // 데스크톱(md+)에서는 중앙 정렬로 되돌린다.
          className="object-cover object-[50%_72%] md:object-center"
        />
      </div>

      {/* 좌측 로그인 영역 — 데스크톱 40~45%.
          모바일에서는 이미지를 제외한 나머지 영역(화면의 약 1/2~3/5)을 채운다. */}
      <div className="order-2 flex w-full flex-1 flex-col px-6 py-8 md:order-1 md:w-[43%] md:flex-none md:px-12 md:py-10 lg:w-[42%] lg:px-16">
        {/* 좌상단 브랜드명 */}
        <div className="text-[20px] font-extrabold tracking-tight text-slate-900">
          Gorafi
        </div>

        {/* 중앙 영역.
            모바일에서는 Gorafi 바로 아래에 컴팩트하게 붙여(justify-start, py-5) 한 카드처럼
            이어 보이게 하고, 데스크톱(md+)에서는 기존처럼 세로 중앙 정렬(justify-center, py-10)로
            되돌려 레이아웃을 그대로 유지한다. */}
        <div className="flex flex-1 flex-col justify-start py-5 md:justify-center md:py-10">
          <div className="mx-auto w-full max-w-sm">
            <h1 className="text-[28px] font-extrabold leading-tight text-slate-900 sm:text-[32px]">
              Welcome back
            </h1>
            <p className="mt-3 text-[14px] leading-relaxed text-slate-500">
              google 계정으로 로그인해주세요.
            </p>

            <button
              type="button"
              onClick={signInWithGoogle}
              disabled={loading || !configured}
              className="mt-8 inline-flex h-12 w-full items-center justify-center gap-3 rounded-xl border border-slate-300 bg-white text-[15px] font-semibold text-slate-700 shadow-sm transition hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
            >
              <GoogleIcon />
              Sign in with Google
            </button>

            {!configured ? (
              <p className="mt-4 text-[12.5px] leading-relaxed text-amber-600">
                Firebase 설정이 없어 로그인을 사용할 수 없습니다. 로컬 미리보기
                모드에서는 자동으로 서비스에 진입합니다.
              </p>
            ) : null}

            {error ? (
              <p className="mt-4 text-[12.5px] leading-relaxed text-red-500">
                {error}
              </p>
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

// Google 멀티컬러 "G" 로고 (4색).
function GoogleIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
      <path
        fill="#4285F4"
        d="M17.64 9.2045c0-.6381-.0573-1.2518-.1636-1.8409H9v3.4814h4.8436c-.2086 1.125-.8427 2.0782-1.7959 2.7164v2.2581h2.9087c1.7018-1.5668 2.6836-3.874 2.6836-6.615z"
      />
      <path
        fill="#34A853"
        d="M9 18c2.43 0 4.4673-.806 5.9564-2.1805l-2.9087-2.2581c-.8059.54-1.8368.859-3.0477.859-2.344 0-4.3282-1.5831-5.036-3.7104H.9573v2.3318C2.4382 15.9832 5.4818 18 9 18z"
      />
      <path
        fill="#FBBC05"
        d="M3.964 10.71c-.18-.54-.2823-1.1168-.2823-1.71s.1023-1.17.2823-1.71V4.9582H.9573C.3477 6.1732 0 7.5477 0 9s.3477 2.8268.9573 4.0418L3.964 10.71z"
      />
      <path
        fill="#EA4335"
        d="M9 3.5795c1.3214 0 2.5077.4541 3.4405 1.346l2.5813-2.5814C13.4636.8918 11.4264 0 9 0 5.4818 0 2.4382 2.0168.9573 4.9582L3.964 7.29C4.6718 5.1627 6.656 3.5795 9 3.5795z"
      />
    </svg>
  );
}
