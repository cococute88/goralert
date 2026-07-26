export type RuleFormMode = "standard" | "single-calendar-event";

export type RuleFormVisibility = {
  perKindFilters: boolean;
  singleEventSummary: boolean;
  messageTemplate: boolean;
  advancedSettings: boolean;
  favoriteAction: boolean;
};

export function ruleFormVisibility(mode: RuleFormMode): RuleFormVisibility {
  const standard = mode === "standard";
  return {
    perKindFilters: standard,
    singleEventSummary: !standard,
    messageTemplate: standard,
    advancedSettings: standard,
    favoriteAction: standard,
  };
}
