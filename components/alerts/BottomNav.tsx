"use client";

// GORALERT-ALERT-SYSTEM Sprint 1 Layer B1 (REQ-040)
// Fixed bottom navigation for the Goralert section. 5 tabs, mobile-first, with
// safe-area padding. Active tab is derived from usePathname().

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, Calendar, Home, ListChecks, Settings } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { cx } from "./ui";

type NavItem = {
  href: string;
  label: string;
  icon: LucideIcon;
};

const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "홈", icon: Home },
  { href: "/alerts", label: "알림", icon: Bell },
  { href: "/calendar", label: "캘린더", icon: Calendar },
  { href: "/history", label: "기록", icon: ListChecks },
  { href: "/settings", label: "설정", icon: Settings },
];

function isActive(pathname: string | null, href: string): boolean {
  if (!pathname) return false;
  if (href === "/") return pathname === "/";
  return pathname === href || pathname.startsWith(`${href}/`);
}

export default function BottomNav() {
  const pathname = usePathname();

  return (
    <nav
      className="fixed inset-x-0 bottom-0 z-40 border-t border-border bg-card/95 backdrop-blur"
      style={{ paddingBottom: "env(safe-area-inset-bottom)" }}
      aria-label="고라알림 메뉴"
    >
      <ul className="mx-auto flex max-w-md items-stretch justify-between">
        {NAV_ITEMS.map((item) => {
          const active = isActive(pathname, item.href);
          const Icon = item.icon;
          return (
            <li key={item.href} className="flex-1">
              <Link
                href={item.href}
                aria-current={active ? "page" : undefined}
                className={cx(
                  "flex h-16 flex-col items-center justify-center gap-1 text-[11px] font-medium transition-colors",
                  active ? "text-accent" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon size={22} strokeWidth={active ? 2.4 : 1.8} />
                <span>{item.label}</span>
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
