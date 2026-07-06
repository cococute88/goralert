// 계절 자동 판별 유틸리티.
// 현재 날짜(월)를 기준으로 봄/여름/가을/겨울 중 하나를 반환한다.
// 코드 수정 없이 매년/매월 자동으로 올바른 계절이 선택되도록 순수 함수로 구현한다.

export type Season = "spring" | "summer" | "fall" | "winter";

// 월(1~12) -> 계절 매핑
//  - 봄: 3~5월
//  - 여름: 6~8월
//  - 가을: 9~11월
//  - 겨울: 12~2월
export function getSeasonForMonth(month: number): Season {
  // month 는 1 기반(1=January ... 12=December)으로 받는다.
  if (month >= 3 && month <= 5) return "spring";
  if (month >= 6 && month <= 8) return "summer";
  if (month >= 9 && month <= 11) return "fall";
  return "winter"; // 12, 1, 2
}

// 주어진(또는 현재) 날짜 기준 계절을 반환한다.
export function getCurrentSeason(date: Date = new Date()): Season {
  // getMonth() 는 0 기반이므로 +1 하여 1 기반 월로 변환한다.
  return getSeasonForMonth(date.getMonth() + 1);
}

// 랜딩 히어로 이미지 변형.
// 기본 4계절에 더해, 일부 계절을 시기별로 세분한다:
//  - 여름: 이른 여름(summer, 6/1~7/15) / 늦여름(beach, 7/16~8/31)
//  - 겨울: 12월은 기존 winter, 1월은 newyear1, 2월은 newyear2
export type LandingHeroVariant =
  | "spring"
  | "summer"
  | "beach"
  | "fall"
  | "winter"
  | "newyear1"
  | "newyear2";

// 주어진(또는 현재) 날짜 기준 랜딩 히어로 변형을 반환한다.
// 여름은 일(day) 단위, 겨울은 월 단위로 세분하고, 나머지 계절은 기본 매핑을 따른다.
// 순수 함수라 월/일/연도가 바뀌어도 코드 수정 없이 자동 반영된다.
export function getLandingHeroVariant(
  date: Date = new Date(),
): LandingHeroVariant {
  const month = date.getMonth() + 1; // 1 기반 월
  const day = date.getDate(); // 1~31

  if (month === 6) return "summer"; // 6월 전체 = 이른 여름
  if (month === 7) return day <= 15 ? "summer" : "beach"; // 7/15 까지 summer, 7/16~ beach
  if (month === 8) return "beach"; // 8월 전체 = 늦여름

  if (month === 1) return "newyear1"; // 1월 = 새해 이미지 1
  if (month === 2) return "newyear2"; // 2월 = 새해 이미지 2
  // 12월은 아래 기본 매핑에서 winter 로 처리된다.

  // 그 외(가을/겨울(12월)/봄)는 기본 계절 매핑을 그대로 사용한다.
  return getSeasonForMonth(month);
}
