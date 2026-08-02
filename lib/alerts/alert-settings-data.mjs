// Shared browser settings normalization. Firestore is the source of truth:
// persisted false must stay false, while legacy/missing fields retain the
// established default-enabled behavior used by the Python worker.
export function defaultAlertSettingsData() {
  return { globalEnabled: true };
}

export function normalizeAlertSettingsData(data) {
  if (!data || typeof data !== "object") return defaultAlertSettingsData();
  return {
    ...data,
    globalEnabled: typeof data.globalEnabled === "boolean" ? data.globalEnabled : true,
  };
}
