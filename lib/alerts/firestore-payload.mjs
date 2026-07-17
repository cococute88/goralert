// Firestore rejects undefined at every depth (including objects inside arrays).
// Keep FieldValue/Timestamp/DocumentReference/Date and other prototype-bearing
// values intact. false, 0, null, empty strings and empty arrays are valid data.
function isPlainObject(value) {
  if (value === null || typeof value !== "object" || Array.isArray(value)) return false;
  const prototype = Object.getPrototypeOf(value);
  return prototype === Object.prototype || prototype === null;
}

function sanitizeFirestoreValue(value) {
  if (value === undefined) return undefined;
  if (Array.isArray(value)) {
    // Alert payload arrays represent sets (channels, marks, event types), not
    // positional tuples, so removing undefined entries cannot change meaning.
    return value.map(sanitizeFirestoreValue).filter((item) => item !== undefined);
  }
  if (isPlainObject(value)) {
    return Object.fromEntries(
      Object.entries(value)
        .map(([key, item]) => [key, sanitizeFirestoreValue(item)])
        .filter(([, item]) => item !== undefined),
    );
  }
  return value;
}

export function sanitizeFirestorePayload(data) {
  return sanitizeFirestoreValue(data);
}
