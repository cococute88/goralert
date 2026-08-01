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
  runTransaction,
  serverTimestamp,
  setDoc,
  updateDoc,
  where,
} from "firebase/firestore";

const emulatorHost = process.env.FIRESTORE_EMULATOR_HOST;
const authHost = process.env.FIREBASE_AUTH_EMULATOR_HOST;

test("owner rules protect scheduler state and production history", {
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
      trigger: { mode: "recurring", recurrence: { kind: "monthlyFirstDay", time: "07:00", tz: "Asia/Seoul" } },
    });
    assert.equal((await getDoc(ruleRef)).data().enabled, true);
    await updateDoc(ruleRef, { enabled: false });
    await assert.rejects(
      () => updateDoc(ruleRef, {
        nextScheduledAt: new Date("2026-07-31T22:00:00Z"),
        scheduleStatus: "processing",
      }),
      (error) => error?.code === "permission-denied",
    );
    await assert.rejects(
      () => setDoc(occurrenceRef, {
        id: occurrenceRef.id,
        eventId: occurrenceRef.id,
        ruleId: "rule",
        isTest: false,
        status: "processing",
        scheduledFor: "2026-07-31T22:00:00+00:00",
      }),
      (error) => error?.code === "permission-denied",
    );

    const testRef = doc(db, "users", uid, "notificationLogs", "rule:test:1");
    await setDoc(testRef, {
      id: testRef.id,
      eventId: testRef.id,
      ruleId: "rule",
      isTest: true,
      status: "sent",
    });

    const cancellationRef = doc(db, "users", uid, "notificationLogs", "rule:cancelled:1");
    await runTransaction(db, async (transaction) => {
      transaction.update(ruleRef, {
        enabled: true,
        trigger: { mode: "recurring", recurrence: { kind: "monthlyLastDay", time: "12:15", tz: "Asia/Seoul" } },
        scheduleStatus: "schedule_changed",
        scheduleChangedAt: serverTimestamp(),
      });
      transaction.set(cancellationRef, {
        id: cancellationRef.id,
        eventId: cancellationRef.id,
        ruleId: "rule",
        isTest: false,
        status: "cancelled",
        failureCode: "schedule_changed",
      });
    });

    const ownHistory = await getDocs(query(
      collection(db, "users", uid, "notificationLogs"),
      where("ruleId", "==", "rule"),
    ));
    assert.equal(ownHistory.size, 2);

    const otherRule = doc(db, "users", `${uid}-other`, "alertRules", "rule");
    await assert.rejects(() => getDoc(otherRule), (error) => error?.code === "permission-denied");
    await assert.rejects(
      () => setDoc(otherRule, { uid: `${uid}-other`, enabled: true }),
      (error) => error?.code === "permission-denied",
    );
  } finally {
    await deleteApp(app);
  }
});
