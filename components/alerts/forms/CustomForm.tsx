"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// 사용자 정의 조건 폼. 표현식 텍스트를 그대로 보관한다. 평가는 Python 엔진의
// custom 평가기(alert_engine/evaluators/custom.py)가 담당한다.
//
// 평가기 능력(정확):
//   - 사용 가능 이름: prev(직전 평가값) + condition.params 의 원시값.
//   - 연산: 산술(+ - * / % **), 비교(> >= < <= == !=), 논리(AND/OR/NOT),
//     화이트리스트 함수(abs, min, max, round, float, int, len).
//   - 미지원: rsi()/vix() 등 시장 데이터 함수 호출(네임스페이스에 없어 평가 에러 →
//     미트리거). 지표 기반 알림은 "RSI · 지표" 종류를 사용해야 한다.

import type { CustomCondition } from "@/lib/alerts/types";
import { Field, TextArea, type RuleFormProps } from "./fields";

export default function CustomForm({ value, onChange }: RuleFormProps) {
  const condition = (value.condition?.kind === "custom" ? value.condition : { kind: "custom", expression: "" }) as CustomCondition;

  return (
    <div className="space-y-3">
      <Field
        label="조건 표현식"
        hint="직전 평가값 prev 와 숫자·연산자로 식을 작성합니다. 예: prev >= 50 · 사용 가능: + - * /, 비교(>, <, ==), AND/OR/NOT, abs/min/max/round"
      >
        <TextArea
          value={condition.expression}
          placeholder="예: prev >= 50"
          onChange={(e) => onChange({ ...value, condition: { kind: "custom", expression: e.target.value, params: condition.params } })}
        />
      </Field>
      <p className="rounded-xl border border-warning/40 bg-warning/10 px-3 py-2 text-[11px] text-warning">
        ⚠️ rsi(), vix() 같은 시장 데이터 함수는 아직 지원되지 않아 해당 식은 평가되지 않습니다. RSI·VIX·가격 등 지표
        알림은 “RSI · 지표” 종류로 만들어 주세요.
      </p>
    </div>
  );
}
