import type { AuditEvent } from "@/lib/types";
import { formatTime } from "@/lib/format";

const ACTOR: Record<AuditEvent["actor"], { label: string; dot: string }> = {
  buyer: { label: "You", dot: "bg-ink" },
  merchant: { label: "Merchant", dot: "bg-warn" },
  system: { label: "IntentLock", dot: "bg-lock" },
  ai: { label: "AI", dot: "bg-muted" },
  paypal: { label: "PayPal", dot: "bg-[#0070ba]" },
};

const STOP_EVENTS = new Set(["APPROVAL_INVALIDATED", "APPROVAL_EXPIRED", "QUOTE_STALE", "QUOTE_EXPIRED", "PAYMENT_BLOCKED"]);

const pretty = (t: string) =>
  t
    .toLowerCase()
    .replace(/_/g, " ")
    .replace(/^./, (c) => c.toUpperCase())
    .replace(/paypal/gi, "PayPal");

/** F9 — event timeline, read from GET /api/quotes/{id}/events. */
export function Timeline({ events }: { events: AuditEvent[] }) {
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <h2 className="text-sm font-semibold">Timeline</h2>
      {events.length === 0 ? (
        <p className="mt-3 text-sm text-muted">No events yet.</p>
      ) : (
        <ol className="relative mt-4 space-y-4 border-l border-line pl-5">
          {events.map((e) => {
            const a = ACTOR[e.actor] ?? ACTOR.system;
            const stop = STOP_EVENTS.has(e.event_type);
            return (
              <li key={e.event_id} className="relative">
                <span className={`absolute -left-[26px] top-1.5 h-2.5 w-2.5 rounded-full ring-4 ring-surface ${stop ? "bg-stop" : a.dot}`} />
                <div className="flex items-baseline justify-between gap-2">
                  <p className={`text-sm font-medium ${stop ? "text-stop" : ""}`}>{pretty(e.event_type)}</p>
                  <time className="tabular shrink-0 text-xs text-muted" dateTime={e.timestamp}>{formatTime(e.timestamp)}</time>
                </div>
                <p className="text-xs text-muted">
                  <span className="font-medium">{a.label}</span> · {e.details}
                </p>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
