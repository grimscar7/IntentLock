import type { Money } from "./types";

export function formatMoney(m: Money): string {
  try {
    return new Intl.NumberFormat("en-US", { style: "currency", currency: m.currency }).format(m.amount_minor / 100);
  } catch {
    return `${(m.amount_minor / 100).toFixed(2)} ${m.currency}`;
  }
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function formatDateTime(iso: string): string {
  return new Date(iso).toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

/** "4:59" style countdown, or null once elapsed. */
export function remaining(iso: string, nowMs: number): string | null {
  const ms = Date.parse(iso) - nowMs;
  if (ms <= 0) return null;
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}
