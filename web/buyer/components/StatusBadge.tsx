import type { Check, QuoteStatus } from "@/lib/types";

const STATUS: Record<QuoteStatus, { label: string; tone: "lock" | "warn" | "stop" | "muted" }> = {
  DRAFT: { label: "Draft", tone: "muted" },
  QUOTED: { label: "Awaiting your approval", tone: "muted" },
  USER_APPROVED: { label: "Approved — ready to pay", tone: "lock" },
  PAYPAL_ORDER_CREATED: { label: "PayPal order created", tone: "lock" },
  PAYER_APPROVED: { label: "Approved in PayPal", tone: "lock" },
  CAPTURE_IN_PROGRESS: { label: "Capturing payment", tone: "lock" },
  PAID: { label: "Paid", tone: "lock" },
  REAPPROVAL_REQUIRED: { label: "Changed — re-approval needed", tone: "stop" },
  EXPIRED: { label: "Expired", tone: "warn" },
  CANCELLED: { label: "Cancelled", tone: "muted" },
  FAILED: { label: "Failed", tone: "stop" },
  PAYMENT_STATUS_UNKNOWN: { label: "Checking payment status", tone: "warn" },
  RECONCILE: { label: "Being reconciled", tone: "warn" },
};

const TONE = {
  lock: "bg-lock-soft text-lock",
  warn: "bg-warn-soft text-warn",
  stop: "bg-stop-soft text-stop",
  muted: "bg-bg text-muted",
};

export function StatusBadge({ status }: { status: QuoteStatus }) {
  const s = STATUS[status];
  return <span className={`rounded-full px-2.5 py-1 text-xs font-medium ${TONE[s.tone]}`}>{s.label}</span>;
}

export function CheckMark({ result }: { result: Check }) {
  if (result === "pass") return <span className="font-medium text-lock" aria-label="meets requirement">✓</span>;
  if (result === "fail") return <span className="font-medium text-stop" aria-label="does not meet requirement">✕</span>;
  return <span className="font-medium text-warn" aria-label="unknown">?</span>;
}
