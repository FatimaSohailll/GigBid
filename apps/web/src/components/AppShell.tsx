"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { api, call } from "@/lib/client";
import { useResource } from "@/lib/useResource";

const LINKS = [
  ["/inbox", "Inbox"],
  ["/pipeline", "Pipeline"],
  ["/messages", "Messages"],
  ["/runs", "Runs"],
  ["/analytics", "Analytics"],
  ["/settings", "Settings"],
] as const;

function StatusBanner() {
  const { data } = useResource(() => call(api.GET("/api/status")), "status", 8000);
  if (data?.marketplace !== "unavailable") return null;
  return (
    <div className="banner" role="alert">
      <strong>Marketplace unavailable.</strong> {data.message}
    </div>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const path = usePathname();
  if (path.startsWith("/login") || path.startsWith("/onboarding")) {
    return <main className="auth-page">{children}</main>;
  }

  async function signOut() {
    await call(api.POST("/api/auth/logout"));
    window.location.href = "/login";
  }

  return (
    <>
      <a className="skip-link" href="#content">
        Skip to content
      </a>
      <header className="topbar">
        <Link className="brand" href="/inbox">
          GigBid
        </Link>
        <nav aria-label="Main">
          {LINKS.map(([href, label]) => (
            <Link key={href} href={href} aria-current={path.startsWith(href) ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
        <button className="ghost" onClick={signOut}>
          Sign out
        </button>
      </header>
      <StatusBanner />
      <main id="content" className="page">
        {children}
      </main>
    </>
  );
}
