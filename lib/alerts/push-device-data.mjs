const PLATFORMS = new Set(["android", "windows", "macos", "ios", "linux", "unknown"]);
const BROWSERS = new Set(["chrome", "samsung-internet", "edge", "safari", "firefox", "unknown"]);
const DEVICE_TYPES = new Set(["mobile", "tablet", "desktop", "unknown"]);

export const MAX_PUSH_REGISTRATIONS = 100;
export const LAST_SEEN_MIN_INTERVAL_MS = 24 * 60 * 60 * 1000;

function trimmed(value, maxLength) {
  return typeof value === "string" ? value.trim().slice(0, maxLength) : "";
}

function enumValue(value, allowed) {
  return allowed.has(value) ? value : "unknown";
}

function validIso(value, fallback) {
  if (typeof value !== "string" || !value.trim() || Number.isNaN(Date.parse(value))) return fallback;
  return value;
}

export function normalizePushTokens(value) {
  if (!Array.isArray(value)) return [];
  const tokens = [];
  const seen = new Set();
  for (const candidate of value) {
    const token = trimmed(candidate, 4096);
    if (!token || seen.has(token)) continue;
    seen.add(token);
    tokens.push(token);
    if (tokens.length >= MAX_PUSH_REGISTRATIONS) break;
  }
  return tokens;
}

export function normalizePushDevices(value) {
  if (!Array.isArray(value)) return [];
  const devices = [];
  const seenIds = new Set();
  const seenTokens = new Set();
  for (const candidate of value) {
    if (!candidate || typeof candidate !== "object" || Array.isArray(candidate)) continue;
    const id = trimmed(candidate.id, 128);
    const token = trimmed(candidate.token, 4096);
    if (!id || !token || seenIds.has(id) || seenTokens.has(token)) continue;
    const now = new Date(0).toISOString();
    const registeredAt = validIso(candidate.registeredAt, now);
    const lastSeenAt = validIso(candidate.lastSeenAt, registeredAt);
    const tokenUpdatedAt = validIso(candidate.tokenUpdatedAt, registeredAt);
    devices.push({
      id,
      token,
      label: trimmed(candidate.label, 80) || "알 수 없는 기기",
      platform: enumValue(candidate.platform, PLATFORMS),
      browser: enumValue(candidate.browser, BROWSERS),
      deviceType: enumValue(candidate.deviceType, DEVICE_TYPES),
      registeredAt,
      lastSeenAt,
      tokenUpdatedAt,
    });
    seenIds.add(id);
    seenTokens.add(token);
    if (devices.length >= MAX_PUSH_REGISTRATIONS) break;
  }
  return devices;
}

export function effectivePushTokens(data) {
  return normalizePushTokens([
    ...normalizePushDevices(data?.pushDevices).map((device) => device.token),
    ...normalizePushTokens(data?.pushTokens),
  ]);
}

export function upsertPushDeviceData(data, input, nowIso, options = {}) {
  const devices = normalizePushDevices(data?.pushDevices);
  const tokens = normalizePushTokens(data?.pushTokens);
  const id = trimmed(input?.id, 128);
  const token = trimmed(input?.token, 4096);
  if (!id || !token) throw new Error("A push registration requires an id and token");

  const previousById = devices.find((device) => device.id === id);
  if (options.requireExistingDeviceId && !previousById) {
    return { pushTokens: effectivePushTokens(data), pushDevices: devices, changed: false };
  }
  const tokenAlreadyRegistered = effectivePushTokens(data).includes(token);
  if (!previousById && !tokenAlreadyRegistered && effectivePushTokens(data).length >= MAX_PUSH_REGISTRATIONS) {
    throw new Error("등록 가능한 알림 기기 수(100대)를 초과했습니다.");
  }

  const now = validIso(nowIso, new Date().toISOString());
  const registeredAt = previousById?.registeredAt ?? now;
  const previousSeen = previousById ? Date.parse(previousById.lastSeenAt) : Number.NaN;
  const shouldRefreshSeen =
    !Number.isFinite(previousSeen) || Date.parse(now) - previousSeen >= LAST_SEEN_MIN_INTERVAL_MS;
  const lastSeenAt = shouldRefreshSeen ? now : previousById?.lastSeenAt ?? now;
  const tokenChanged = !previousById || previousById.token !== token;
  const tokenUpdatedAt = tokenChanged ? now : previousById.tokenUpdatedAt;
  const nextDevice = {
    id,
    token,
    label: trimmed(input.label, 80) || "알 수 없는 기기",
    platform: enumValue(input.platform, PLATFORMS),
    browser: enumValue(input.browser, BROWSERS),
    deviceType: enumValue(input.deviceType, DEVICE_TYPES),
    registeredAt,
    lastSeenAt,
    tokenUpdatedAt,
  };

  // A token may only belong to one device entry. Re-registering the same local
  // id replaces its old token, while a legacy copy of the new token is linked
  // to this metadata entry without creating a duplicate delivery target.
  const nextDevices = devices.filter((device) => device.id !== id && device.token !== token);
  nextDevices.push(nextDevice);
  const supersededToken = previousById && previousById.token !== token ? previousById.token : null;
  const nextTokens = normalizePushTokens([
    ...tokens.filter((current) => current !== supersededToken),
    ...nextDevices.map((device) => device.token),
  ]);
  return {
    pushTokens: nextTokens,
    pushDevices: nextDevices,
    changed: true,
  };
}

export function removePushRegistrationsData(data, targets) {
  const devices = normalizePushDevices(data?.pushDevices);
  const tokens = normalizePushTokens(data?.pushTokens);
  const deviceIds = new Set();
  const exactTokens = new Set();
  for (const target of Array.isArray(targets) ? targets : []) {
    const id = trimmed(target?.deviceId, 128);
    const token = trimmed(target?.token, 4096);
    if (id) deviceIds.add(id);
    if (token) exactTokens.add(token);
  }
  for (const device of devices) {
    if (deviceIds.has(device.id)) exactTokens.add(device.token);
  }
  const nextDevices = devices.filter(
    (device) => !deviceIds.has(device.id) && !exactTokens.has(device.token),
  );
  const nextTokens = tokens.filter((token) => !exactTokens.has(token));
  const removedCount = effectivePushTokens(data).length - effectivePushTokens({
    pushTokens: nextTokens,
    pushDevices: nextDevices,
  }).length;
  return { pushTokens: nextTokens, pushDevices: nextDevices, removedCount };
}

export function resetPushRegistrationData() {
  return { pushTokens: [], pushDevices: [] };
}
