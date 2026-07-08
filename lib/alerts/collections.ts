// GORALERT-ALERT-SYSTEM Sprint 1 (data foundation layer)
// Firestore path constants/helpers for the alert system subcollections under
// users/{uid}. Mirrors the users/{uid}/... collection layout used across
// lib/firebase/firestore-repositories.ts.

import { collection, doc, type CollectionReference, type DocumentReference, type Firestore } from "firebase/firestore";

// Collection ids (segments) under users/{uid}.
export const ALERT_COLLECTIONS = {
  alertRules: "alertRules",
  notificationLogs: "notificationLogs",
  alertSettings: "alertSettings",
  alertTemplates: "alertTemplates",
  calendarAlertMarks: "calendarAlertMarks",
  // Browser -> engine bridge: the test buttons enqueue a request here that the
  // Python engine drains through the production delivery path (PushChannel).
  testPushRequests: "testPushRequests",
} as const;

// Singleton doc id for the per-user alert settings document.
export const ALERT_SETTINGS_DOC_ID = "default";

// Path helpers --------------------------------------------------------------

export function alertRulesColPath(uid: string): [string, string, string] {
  return ["users", uid, ALERT_COLLECTIONS.alertRules];
}

export function notificationLogsColPath(uid: string): [string, string, string] {
  return ["users", uid, ALERT_COLLECTIONS.notificationLogs];
}

export function alertTemplatesColPath(uid: string): [string, string, string] {
  return ["users", uid, ALERT_COLLECTIONS.alertTemplates];
}

export function calendarAlertMarksColPath(uid: string): [string, string, string] {
  return ["users", uid, ALERT_COLLECTIONS.calendarAlertMarks];
}

export function alertSettingsDocPath(uid: string): [string, string, string, string] {
  return ["users", uid, ALERT_COLLECTIONS.alertSettings, ALERT_SETTINGS_DOC_ID];
}

// Ref factories -------------------------------------------------------------

export function alertRulesCol(db: Firestore, uid: string): CollectionReference {
  return collection(db, ...alertRulesColPath(uid));
}

export function alertRuleDoc(db: Firestore, uid: string, id: string): DocumentReference {
  return doc(db, ...alertRulesColPath(uid), id);
}

export function notificationLogsCol(db: Firestore, uid: string): CollectionReference {
  return collection(db, ...notificationLogsColPath(uid));
}

export function notificationLogDoc(db: Firestore, uid: string, id: string): DocumentReference {
  return doc(db, ...notificationLogsColPath(uid), id);
}

export function alertTemplatesCol(db: Firestore, uid: string): CollectionReference {
  return collection(db, ...alertTemplatesColPath(uid));
}

export function alertTemplateDoc(db: Firestore, uid: string, id: string): DocumentReference {
  return doc(db, ...alertTemplatesColPath(uid), id);
}

export function calendarAlertMarksCol(db: Firestore, uid: string): CollectionReference {
  return collection(db, ...calendarAlertMarksColPath(uid));
}

export function calendarAlertMarkDoc(db: Firestore, uid: string, id: string): DocumentReference {
  return doc(db, ...calendarAlertMarksColPath(uid), id);
}

export function alertSettingsDoc(db: Firestore, uid: string): DocumentReference {
  return doc(db, ...alertSettingsDocPath(uid));
}

export function testPushRequestsColPath(uid: string): [string, string, string] {
  return ["users", uid, ALERT_COLLECTIONS.testPushRequests];
}

export function testPushRequestsCol(db: Firestore, uid: string): CollectionReference {
  return collection(db, ...testPushRequestsColPath(uid));
}

export function testPushRequestDoc(db: Firestore, uid: string, id: string): DocumentReference {
  return doc(db, ...testPushRequestsColPath(uid), id);
}
