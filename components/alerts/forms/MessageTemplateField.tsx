"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// 공통 후행 필드: 사용자 정의 메시지 (제목/본문). {ticker}/{value}/{threshold}/{name}
// 치환 변수 안내 포함.

import { Field, TextInput, TextArea, type RuleFormProps } from "./fields";

export default function MessageTemplateField({ value, onChange }: RuleFormProps) {
  const message = value.delivery?.message ?? { title: "", body: "" };

  const update = (patch: Partial<{ title: string; body: string }>) => {
    onChange({
      ...value,
      delivery: {
        channels: value.delivery?.channels ?? ["telegram", "push"],
        ...value.delivery,
        message: { title: message.title, body: message.body, ...patch },
      },
    });
  };

  return (
    <div className="space-y-3">
      <Field label="알림 제목">
        <TextInput
          value={message.title}
          placeholder="예: VR 주문"
          onChange={(e) => update({ title: e.target.value })}
        />
      </Field>
      <Field
        label="알림 본문"
        hint="치환 변수: {ticker} 종목 · {value} 현재값 · {threshold} 기준값 · {name} 알림 이름"
      >
        <TextArea
          value={message.body}
          placeholder="예: {ticker} 매수 마감일입니다"
          onChange={(e) => update({ body: e.target.value })}
        />
      </Field>
    </div>
  );
}
