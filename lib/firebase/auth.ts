"use client";

import { useEffect, useState } from "react";
import {
  GoogleAuthProvider,
  browserLocalPersistence,
  getRedirectResult,
  onAuthStateChanged,
  setPersistence,
  signInWithPopup,
  signInWithRedirect,
  signOut,
  type User,
} from "firebase/auth";
import { doc, getDoc, setDoc, serverTimestamp } from "firebase/firestore";
import { firebaseAuth, firestoreDb, isFirebaseConfigured } from "./client";

async function ensureUserProfile(user: User): Promise<void> {
  if (!firestoreDb) return;
  const ref = doc(firestoreDb, "users", user.uid);
  const existing = await getDoc(ref);
  await setDoc(
    ref,
    {
      uid: user.uid,
      email: user.email ?? null,
      displayName: user.displayName ?? null,
      photoURL: user.photoURL ?? null,
      createdAt: existing.exists() ? existing.data().createdAt : serverTimestamp(),
      updatedAt: serverTimestamp(),
    },
    { merge: true },
  );
}

export type AuthState = {
  user: User | null;
  loading: boolean;
  error: string | null;
  configured: boolean;
  signInWithGoogle: () => Promise<void>;
  logout: () => Promise<void>;
};

let persistenceReady: Promise<void> | null = null;
let redirectResultReady: Promise<void> | null = null;

function authLog(step: string, details: Record<string, unknown> = {}) {
  if (typeof console === "undefined") return;
  console.info(`[auth] ${step}`, {
    ...details,
    location: typeof window !== "undefined" ? window.location.href : "server",
    authDomain: process.env.NEXT_PUBLIC_FIREBASE_AUTH_DOMAIN || null,
    appHost: typeof window !== "undefined" ? window.location.host : null,
    userAgent: typeof navigator !== "undefined" ? navigator.userAgent : null,
  });
}

function storageDiagnostics() {
  if (typeof window === "undefined") return {};
  const cookies = document.cookie ? document.cookie.split(";").map((cookie) => cookie.trim().split("=")[0]) : [];
  const storage: Record<string, unknown> = { cookieNames: cookies };
  try {
    const key = "gorani-auth-storage-probe";
    window.localStorage.setItem(key, "1");
    storage.localStorageWritable = window.localStorage.getItem(key) === "1";
    window.localStorage.removeItem(key);
  } catch (err) {
    storage.localStorageWritable = false;
    storage.localStorageError = err instanceof Error ? err.message : String(err);
  }
  try {
    const key = "gorani-auth-session-probe";
    window.sessionStorage.setItem(key, "1");
    storage.sessionStorageWritable = window.sessionStorage.getItem(key) === "1";
    window.sessionStorage.removeItem(key);
  } catch (err) {
    storage.sessionStorageWritable = false;
    storage.sessionStorageError = err instanceof Error ? err.message : String(err);
  }
  return storage;
}

function userSummary(user: User | null) {
  return user
    ? {
        uid: user.uid,
        email: user.email,
        displayName: user.displayName,
        providerIds: user.providerData.map((provider) => provider.providerId),
      }
    : null;
}

export function ensureFirebaseLocalPersistence(): Promise<void> {
  if (!firebaseAuth) return Promise.resolve();
  if (!persistenceReady) {
    authLog("persistence:start", storageDiagnostics());
    persistenceReady = setPersistence(firebaseAuth, browserLocalPersistence)
      .then(() => {
        authLog("persistence:ready", storageDiagnostics());
      })
      .catch((err) => {
        persistenceReady = null;
        authLog("persistence:error", { error: err instanceof Error ? err.message : String(err), ...storageDiagnostics() });
        throw err;
      });
  }
  return persistenceReady;
}

function isPopupBlockedError(err: unknown): boolean {
  const code = typeof err === "object" && err && "code" in err ? String((err as { code?: unknown }).code) : "";
  return [
    "auth/popup-blocked",
    "auth/popup-closed-by-user",
    "auth/cancelled-popup-request",
    "auth/operation-not-supported-in-this-environment",
  ].includes(code);
}

function googleProvider() {
  const provider = new GoogleAuthProvider();
  provider.setCustomParameters({ prompt: "select_account" });
  return provider;
}

export function handleGoogleRedirectResult(): Promise<void> {
  if (!firebaseAuth) return Promise.resolve();
  const auth = firebaseAuth;
  if (!redirectResultReady) {
    authLog("redirect-result:start", storageDiagnostics());
    redirectResultReady = ensureFirebaseLocalPersistence()
      .then(() => getRedirectResult(auth))
      .then(async (credential) => {
        authLog("redirect-result:complete", {
          hasCredential: Boolean(credential),
          user: userSummary(credential?.user || null),
          operationType: credential?.operationType || null,
          ...storageDiagnostics(),
        });
        if (credential?.user) {
          authLog("session:create:start", { user: userSummary(credential.user) });
          await ensureUserProfile(credential.user);
          authLog("session:create:profile-ready", { user: userSummary(credential.user) });
        }
      })
      .catch((err) => {
        authLog("redirect-result:error", { error: err instanceof Error ? err.message : String(err), ...storageDiagnostics() });
        console.warn("Google redirect result handling failed", err);
      });
  }
  return redirectResultReady;
}

export function useFirebaseAuth(): AuthState {
  const [user, setUser] = useState<User | null>(null);
  const [loading, setLoading] = useState(isFirebaseConfigured);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    authLog("client-state:init", { configured: isFirebaseConfigured, ...storageDiagnostics() });
    if (!firebaseAuth) {
      setLoading(false);
      return undefined;
    }
    const auth = firebaseAuth;

    let active = true;
    let authStateResolved = false;
    let redirectResolved = false;
    const finishHydration = () => {
      if (active && authStateResolved && redirectResolved) {
        authLog("client-state:hydrated", { user: userSummary(auth.currentUser), ...storageDiagnostics() });
        setLoading(false);
      }
    };

    ensureFirebaseLocalPersistence()
      .then(() => handleGoogleRedirectResult())
      .catch((err) => {
        console.warn("Failed to prepare Firebase Auth persistence", err);
        if (active) setError("로그인 유지 설정을 준비하지 못했습니다. 브라우저 저장소 설정을 확인해 주세요.");
      })
      .finally(() => {
        redirectResolved = true;
        finishHydration();
      });

    const unsubscribe = onAuthStateChanged(auth, async (nextUser) => {
      authLog("client-state:onAuthStateChanged", { user: userSummary(nextUser), ...storageDiagnostics() });
      setUser(nextUser);
      authStateResolved = true;
      finishHydration();
      if (nextUser) {
        authLog("session:create:start", { user: userSummary(nextUser) });
        await ensureUserProfile(nextUser).catch((err) => {
          authLog("session:create:profile-error", { error: err instanceof Error ? err.message : String(err), user: userSummary(nextUser) });
          console.warn("Failed to ensure Firebase user profile", err);
        });
        authLog("session:create:profile-ready", { user: userSummary(nextUser) });
      }
    });

    return () => {
      active = false;
      unsubscribe();
    };
  }, []);

  const signInWithGoogle = async () => {
    if (!firebaseAuth) {
      setError("Firebase 설정이 없어 로컬 미리보기 모드로 동작합니다.");
      return;
    }

    setError(null);
    authLog("login:start", storageDiagnostics());
    try {
      await ensureFirebaseLocalPersistence();
      const credential = await signInWithPopup(firebaseAuth, googleProvider());
      authLog("login:popup:complete", { user: userSummary(credential.user), ...storageDiagnostics() });
      if (credential.user) await ensureUserProfile(credential.user);
    } catch (err) {
      authLog("login:popup:error", { code: typeof err === "object" && err && "code" in err ? String((err as { code?: unknown }).code) : null, error: err instanceof Error ? err.message : String(err), ...storageDiagnostics() });
      if (isPopupBlockedError(err)) {
        try {
          authLog("login:redirect:start", storageDiagnostics());
          await ensureFirebaseLocalPersistence();
          await signInWithRedirect(firebaseAuth, googleProvider());
          return;
        } catch (redirectErr) {
          authLog("login:redirect:error", { error: redirectErr instanceof Error ? redirectErr.message : String(redirectErr), ...storageDiagnostics() });
          console.warn("Google redirect fallback failed", redirectErr);
        }
      }
      console.warn("Google sign-in failed", err);
      setError("Google 로그인에 실패했습니다. 잠시 후 다시 시도해 주세요.");
    }
  };

  const logout = async () => {
    if (!firebaseAuth) return;
    setError(null);
    try {
      await signOut(firebaseAuth);
      authLog("logout:complete", storageDiagnostics());
    } catch (err) {
      console.warn("Sign-out failed", err);
      setError("로그아웃에 실패했습니다. 잠시 후 다시 시도해 주세요.");
    }
  };

  return { user, loading, error, configured: isFirebaseConfigured, signInWithGoogle, logout };
}
