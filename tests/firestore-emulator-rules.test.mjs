import test from "node:test";
import assert from "node:assert/strict";
import { deleteApp, initializeApp } from "firebase/app";
import {
  connectAuthEmulator,
  getAuth,
  signInAnonymously,
} from "firebase/auth";
import {
  collection,
  connectFirestoreEmulator,
  doc,
  getDoc,
  getDocs,
  getFirestore,
  query,
  setDoc,
  where,
} from "firebase/firestore";

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;
const authHost = process.env.FIREBASE_AUTH_EMULATOR_HOST;

test("owner rules allow durable rule/history access and reject cross-user access", {
  skip: !emulatorHost || !authHost,
}, async () => {
  const projectId = process.env.GCLOUD_PROJECT || "demo-goralert";
  const app = initializeApp({ projectId, apiKey: "demo-api-key" }, `rules-${Date.now()}`);
  const auth = getAuth(app);
  connectAuthEmulator(auth, `http://${authHost}`, { disableWarnings: true });
  const credential = await signInAnonymously(auth);
  const uid = credential.user.uid;
  const db = getFirestore(app);
  const [host, port] = emulatorHost.split(":");
  connectFirestoreEmulator(db, host, Number(port));

  try {
    const ruleRef = doc(db, "users", uid, "alertRules", "rule");
    const occurrenceRef = doc(db, "users", uid, "notificationLogs", "rule:2026-08-01T07:00:00+09:00");
    await setDoc(ruleRef, {
      uid,
      enabled: true,
      nextScheduledAt: new Date("2026-07-31T22:00:00Z"),
      trigger: { mode: "recurring", recurrence: { kind: "monthlyFirstDay", time: "07:00", tz: "Asia/Seoul" } },
    });
    await setDoc(occurrenceRef, {
      eventId: occurrenceRef.id,
      ruleId: "rule",
      status: "processing",
      scheduledFor: "2026-07-31T22:00:00+00:00",
    });
    assert.equal((await getDoc(ruleRef)).data().enabled, true);
    const ownHistory = await getDocs(query(
      collection(db, "users", uid, "notificationLogs"),
      where("ruleId", "==", "rule"),
    ));
    assert.equal(ownHistory.size, 1);

    const otherRule = doc(db, "users", `${uid}-other`, "alertRules", "rule");
    await assert.rejects(() => getDoc(otherRule), (error) => error?.code === "permission-denied");
    await assert.rejects(
      () => setDoc(otherRule, { enabled: true }),
      (error) => error?.code === "permission-denied",
    );
  } finally {
    await deleteApp(app);
  }
});
