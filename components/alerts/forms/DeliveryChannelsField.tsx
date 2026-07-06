"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// 공통 후행 필드: 전송 채널 (telegram / push). 둘 다 기본 ON.

import type { DeliveryChannel } from "@/lib/alerts/types";
import { Field, type RuleFormProps } from "./fields";

const CHANNELS: { value: DeliveryChannel; label: string }[] = [
  { value: "telegram", label: "텔레그램" },
  { value: "push", label: "푸시 알림" },
];

export default function DeliveryChannelsField({ value, onChange }: RuleFormProps) {
  const channels = value.delivery?.channels ?? ["telegram", "push"];

  const toggle = (channel: DeliveryChannel) => {
    const has = channels.includes(channel);
    const next = has ? channels.filter((c) => c !== channel) : [...channels, channel];
    onChange({
      ...value,
      delivery: { ...value.delivery, channels: next },
    });
  };

  return (
    <Field label="전송 채널" hint="하나 이상 선택하세요. 기본값은 둘 다 켜짐입니다.">
      <div className="flex gap-2">
        {CHANNELS.map((channel) => {
          const active = channels.includes(channel.value);
          return (
            <label
              key={channel.value}
              className={`flex flex-1 cursor-pointer items-center justify-center gap-2 rounded-xl border px-3 py-2 text-sm ${
                active
                  ? "border-accent bg-accent/10 text-foreground"
                  : "border-border bg-background text-muted-foreground"
              }`}
            >
              <input
                type="checkbox"
                className="accent-[rgb(var(--accent))]"
                checked={active}
                onChange={() => toggle(channel.value)}
              />
              {channel.label}
            </label>
          );
        })}
      </div>
    </Field>
  );
}
