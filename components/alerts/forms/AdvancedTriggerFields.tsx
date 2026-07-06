"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1
// 공통 후행 필드(고급): 쿨다운 + 방해 금지 시간(quietHours). "고급" 디스클로저
// 안에서만 보이도록 부모가 토글한다.

import { Field, NumberInput, TimeInput, TextInput, SEOUL_TZ, type RuleFormProps } from "./fields";

export default function AdvancedTriggerFields({ value, onChange }: RuleFormProps) {
  const trigger = value.trigger ?? { mode: "recurring" as const };
  const quietHours = trigger.quietHours;

  const updateTrigger = (patch: Partial<typeof trigger>) => {
    onChange({ ...value, trigger: { ...trigger, ...patch } });
  };

  const updateQuiet = (patch: Partial<NonNullable<typeof quietHours>>) => {
    const base = quietHours ?? { start: "22:00", end: "08:00", tz: SEOUL_TZ };
    updateTrigger({ quietHours: { ...base, ...patch } });
  };

  const quietEnabled = Boolean(quietHours);

  return (
    <div className="space-y-3 rounded-xl border border-border bg-muted/40 p-3">
      <Field label="재발송 제한 (쿨다운, 분)" hint="같은 알림을 다시 보내기까지의 최소 간격입니다.">
        <NumberInput
          min={0}
          value={trigger.cooldownMinutes ?? ""}
          placeholder="예: 1440 (하루)"
          onChange={(e) => {
            const raw = e.target.value;
            updateTrigger({ cooldownMinutes: raw === "" ? undefined : Number(raw) });
          }}
        />
      </Field>

      <label className="flex items-center gap-2 text-sm text-foreground">
        <input
          type="checkbox"
          className="accent-[rgb(var(--accent))]"
          checked={quietEnabled}
          onChange={(e) => updateTrigger({ quietHours: e.target.checked ? { start: "22:00", end: "08:00", tz: SEOUL_TZ } : undefined })}
        />
        방해 금지 시간 사용
      </label>

      {quietEnabled ? (
        <div className="grid grid-cols-2 gap-2">
          <Field label="시작">
            <TimeInput value={quietHours?.start ?? "22:00"} onChange={(e) => updateQuiet({ start: e.target.value })} />
          </Field>
          <Field label="종료">
            <TimeInput value={quietHours?.end ?? "08:00"} onChange={(e) => updateQuiet({ end: e.target.value })} />
          </Field>
          <div className="col-span-2">
            <Field label="시간대(tz)">
              <TextInput value={quietHours?.tz ?? SEOUL_TZ} onChange={(e) => updateQuiet({ tz: e.target.value })} />
            </Field>
          </div>
        </div>
      ) : null}
    </div>
  );
}
