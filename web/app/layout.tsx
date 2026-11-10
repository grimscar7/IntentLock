import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { USE_MOCKS } from "@/lib/api";

export const metadata: Metadata = {
  title: "IntentLock",
  description: "AI shopping assistant with consent-controlled PayPal Sandbox checkout",
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">
        <header className="border-b border-line bg-surface">
          <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
            <Link href="/" className="flex items-center gap-2 font-semibold tracking-tight">
              <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden className="text-lock">
                <rect x="4" y="10" width="16" height="11" rx="2" fill="currentColor" />
                <path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="currentColor" strokeWidth="2" fill="none" />
              </svg>
              IntentLock
            </Link>
            <nav className="flex items-center gap-1 text-sm">
              <Link href="/" className="rounded-md px-3 py-1.5 hover:bg-bg">Shop</Link>
              <Link href="/merchant" className="rounded-md px-3 py-1.5 hover:bg-bg">Merchant panel</Link>
              {USE_MOCKS && (
                <span className="ml-2 rounded-full bg-warn-soft px-2 py-0.5 text-xs font-medium text-warn" title="Using in-browser mock data">
                  Mock data
                </span>
              )}
            </nav>
          </div>
        </header>
        <main className="mx-auto max-w-6xl px-4 py-8">{children}</main>
        <footer className="mx-auto max-w-6xl px-4 pb-8 text-xs text-muted">
          Demo store with test data only. Payments run in PayPal Sandbox — no real money moves.
        </footer>
      </body>
    </html>
  );
}
