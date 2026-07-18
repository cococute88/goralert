import type { Metadata } from "next";
import "./globals.css";
import { ThemeProvider } from "@/components/theme/ThemeProvider";
import AuthGate from "@/components/auth/AuthGate";
import BottomNav from "@/components/alerts/BottomNav";
import { ToastProvider } from "@/components/alerts/ui/toast";
import PushServiceWorker from "@/components/alerts/PushServiceWorker";
import PushForegroundMessages from "@/components/alerts/PushForegroundMessages";
import { Bell } from "lucide-react";

const APP_NAME = "고라알림";
const APP_DESCRIPTION = "고라알림 — 투자 일정·조건 알림 대시보드";

export const metadata: Metadata = {
  applicationName: APP_NAME,
  title: {
    default: APP_NAME,
    template: `%s | ${APP_NAME}`,
  },
  description: APP_DESCRIPTION,
  manifest: "/manifest.webmanifest",
  appleWebApp: {
    capable: true,
    title: APP_NAME,
    statusBarStyle: "default",
  },
  openGraph: {
    type: "website",
    siteName: APP_NAME,
    title: APP_NAME,
    description: APP_DESCRIPTION,
    images: ["/gorani-logo.png"],
  },
  twitter: {
    card: "summary",
    title: APP_NAME,
    description: APP_DESCRIPTION,
    images: ["/gorani-logo.png"],
  },
  icons: {
    icon: [
      { url: "/gorani-bell-32.png", sizes: "32x32", type: "image/png" },
      { url: "/gorani-bell-192.png", sizes: "192x192", type: "image/png" },
    ],
    shortcut: [{ url: "/gorani-bell-32.png", sizes: "32x32", type: "image/png" }],
    apple: [{ url: "/gorani-bell-180.png", sizes: "180x180", type: "image/png" }],
  },
};

const themeInitScript = `(function(){try{var k='gorani-theme';var p=localStorage.getItem(k);var s=window.matchMedia('(prefers-color-scheme: dark)').matches?'dark':'light';var t=(p==='light'||p==='dark')?p:(p==='system'?s:'light');var r=document.documentElement;r.classList.remove('light','dark');r.classList.add(t);r.style.colorScheme=t;}catch(e){var r2=document.documentElement;r2.classList.add('light');r2.style.colorScheme='light';}})();`;

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="ko" className="light" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <ThemeProvider>
          <PushServiceWorker />
          <AuthGate>
            <ToastProvider>
              <PushForegroundMessages />
              <div className="min-h-screen bg-background text-foreground">
                <header className="sticky top-0 z-30 border-b border-border bg-background/95 backdrop-blur">
                  <div className="mx-auto flex max-w-md items-center gap-2 px-4 py-3">
                    <Bell size={20} className="text-accent" />
                    <span className="text-base font-semibold">고라알림</span>
                  </div>
                </header>
                <main className="mx-auto max-w-md px-4 pb-28 pt-4">{children}</main>
                <BottomNav />
              </div>
            </ToastProvider>
          </AuthGate>
        </ThemeProvider>
      </body>
    </html>
  );
}
