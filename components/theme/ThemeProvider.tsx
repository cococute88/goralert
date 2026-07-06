"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

export type ThemePreference = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

export const THEME_STORAGE_KEY = "gorani-theme";

type ThemeContextValue = {
  /** User's explicit choice: light / dark / system. */
  preference: ThemePreference;
  /** Concrete theme currently applied (system resolved to light/dark). */
  theme: ResolvedTheme;
  setPreference: (preference: ThemePreference) => void;
};

const ThemeContext = createContext<ThemeContextValue | null>(null);

function systemTheme(): ResolvedTheme {
  if (typeof window === "undefined") return "light";
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStoredPreference(): ThemePreference {
  if (typeof window === "undefined") return "light";
  try {
    const raw = window.localStorage.getItem(THEME_STORAGE_KEY);
    if (raw === "light" || raw === "dark" || raw === "system") return raw;
  } catch {
    /* ignore storage access errors (private mode, etc.) */
  }
  return "light";
}

function applyTheme(theme: ResolvedTheme) {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.classList.toggle("dark", theme === "dark");
  root.classList.toggle("light", theme === "light");
  root.style.colorScheme = theme;
}

/**
 * App-wide theme provider. SSR and the first client render both default to
 * "light" for new users so hydration markup matches; the stored
 * preference is applied in an effect right after mount. The inline script in
 * the root layout has already set the correct <html> class before paint, so
 * new light-mode users see no flash while explicit dark users keep their stored choice.
 */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [preference, setPreferenceState] = useState<ThemePreference>("light");
  const [theme, setTheme] = useState<ResolvedTheme>("light");

  useEffect(() => {
    const stored = readStoredPreference();
    const resolved = stored === "system" ? systemTheme() : stored;
    setPreferenceState(stored);
    setTheme(resolved);
    applyTheme(resolved);
  }, []);

  // Track OS theme changes while the user is on "system".
  useEffect(() => {
    if (preference !== "system" || typeof window === "undefined") return;
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => {
      const resolved: ResolvedTheme = mq.matches ? "dark" : "light";
      setTheme(resolved);
      applyTheme(resolved);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, [preference]);

  const setPreference = useCallback((next: ThemePreference) => {
    setPreferenceState(next);
    try {
      window.localStorage.setItem(THEME_STORAGE_KEY, next);
    } catch {
      /* ignore */
    }
    const resolved = next === "system" ? systemTheme() : next;
    setTheme(resolved);
    applyTheme(resolved);
  }, []);

  return (
    <ThemeContext.Provider value={{ preference, theme, setPreference }}>
      {children}
    </ThemeContext.Provider>
  );
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    // Safe fallback if a component renders outside the provider.
    return { preference: "light", theme: "light", setPreference: () => {} };
  }
  return ctx;
}

/** Convenience hook returning only the resolved "light" | "dark" value. */
export function useResolvedTheme(): ResolvedTheme {
  return useTheme().theme;
}
