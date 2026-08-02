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

// True only after the authoritative listener has observed every field written
// by a settings save. `undefined` means the caller intended to delete the
// optional field rather than leave the previous persisted value in place.
export function alertSettingsReflectsUpdate(settings, partial) {
  if (!settings || typeof settings !== "object" || !partial || typeof partial !== "object") {
    return false;
  }
  return Object.entries(partial).every(([key, expected]) => (
    expected === undefined
      ? !Object.prototype.hasOwnProperty.call(settings, key)
      : Object.is(settings[key], expected)
  ));
}
