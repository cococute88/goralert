import type { Config } from "tailwindcss";

// rgb(var(--token) / <alpha-value>) lets utilities like bg-card/40 keep working.
const withAlpha = (token: string) => `rgb(var(${token}) / <alpha-value>)`;

const config: Config = {
  // Theme is toggled by adding `dark`/`light` class on <html> (see ThemeProvider).
  darkMode: "class",
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        // Design tokens backed by CSS variables in globals.css (light + dark).
        background: withAlpha("--background"),
        foreground: withAlpha("--foreground"),
        card: withAlpha("--card"),
        "card-foreground": withAlpha("--card-foreground"),
        border: withAlpha("--border"),
        muted: withAlpha("--muted"),
        "muted-foreground": withAlpha("--muted-foreground"),
        accent: withAlpha("--accent"),
        "accent-foreground": withAlpha("--accent-foreground"),
        success: withAlpha("--success"),
        danger: withAlpha("--danger"),
        warning: withAlpha("--warning"),
        pf: {
          blue: "#2563eb",
          up: "#e5484d",
          down: "#3b82f6",
        },
      },
    },
  },
  plugins: [],
};

export default config;
