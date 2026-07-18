import type { MetadataRoute } from "next";

// PWA / mobile home-screen metadata. The user-facing app name is unified to
// "고라알림" across the browser tab, OpenGraph, and the installable PWA
// (REQ-021 / REQ-003.5 label-only rebrand). Internal package name is unchanged.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "고라알림",
    short_name: "고라알림",
    description: "고라알림 — 투자 일정·조건 알림 대시보드",
    start_url: "/",
    display: "standalone",
    background_color: "#ffffff",
    theme_color: "#ffffff",
    icons: [
      {
        src: "/gorani-bell-192.png",
        sizes: "192x192",
        type: "image/png",
      },
      {
        src: "/gorani-bell-512.png",
        sizes: "512x512",
        type: "image/png",
      },
    ],
  };
}
