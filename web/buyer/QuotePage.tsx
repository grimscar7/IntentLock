"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { api, toApiError } from "@/lib/api";
import type { ApiError } from "@/lib/errors";
import type { AuditEvent, QuoteDetail } from "@/lib/types";
import { ApprovalCard, ApprovedSnapshot } from "./components/ApprovalCard";
import { ChangeNotice } from "./components/ChangeNotice";
import { ErrorBanner } from "./components/ErrorBanner";
import { PaymentArea } from "./components/PaymentArea";
import { Receipt } from "./components/Receipt";
import { StatusBadge } from "./components/StatusBadge";
import { Timeline } from "./components/Timeline";

const POLL_MS = 3000;

export default function QuotePage({ quoteId }: { quoteId: string }) {
  const router = useRouter();
  const [quote, setQuote] = useState<QuoteDetail | null>(null);
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<ApiError | null>(null);
  const [loadError, setLoadError] = useState<ApiError | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => Date.now());

  const refresh = useCallback(async () => {
    try {
      const [q, ev] = await Promise.all([api.getQuote(quoteId), api.getQuoteEvents(quoteId)]);
      setQuote(q);
      setEvents(ev);
      setLoadError(null);
    } catch (e) {
      setLoadError(toApiError(e));
    }
  }, [quoteId]);

  // Poll so a merchant change made elsewhere shows up without a reload.
  useEffect(() => {
    refresh();
    const poll = setInterval(refresh, POLL_MS);
    const tick = setInterval(() => setNow(Date.now()), 1000);
    return () => {
      clearInterval(poll);
      clearInterval(tick);
    };
  }, [refresh]);

  async function approve() {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      await api.approve(quoteId);
      await refresh();
    } catch (e) {
      setError(toApiError(e));
      await refresh();
    } finally {
      setBusy(false);
    }
  }

  async function requote(productId?: string) {
    if (!quote) return;
    setBusy(true);
    setError(null);
    try {
      const { quotes } = await api.createQuotes(quote.intent_id, productId);
      if (quotes[0]) router.push(`/quote/${quotes[0].quote_id}`);
    } catch (e) {
      setError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }

  if (loadError && !quote) {
    return (
      <div className="space-y-4">
        <ErrorBanner error={loadError} onRetry={refresh} />
        <Link href="/" className="text-sm text-lock underline">Back to search</Link>
      </div>
    );
  }
  if (!quote) return <QuoteSkeleton />;

  const s = quote.status;
  const blocked = s === "REAPPROVAL_REQUIRED" || s === "EXPIRED";
  const pending = s === "PAYMENT_STATUS_UNKNOWN" || s === "RECONCILE" || s === "CAPTURE_IN_PROGRESS";

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <Link href="/" className="text-sm text-muted hover:text-ink">← Back to matches</Link>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{quote.product.name}</h1>
          <p className="font-mono text-xs text-muted">Quote {quote.quote_id}</p>
        </div>
        <StatusBadge status={s} />
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <div className="space-y-4">
          <ErrorBanner error={error} />
          {notice && (
            <div className="rounded-lg border border-line bg-surface p-3 text-sm" role="status">
              {notice}
            </div>
          )}

          {s === "QUOTED" && <ApprovalCard quote={quote} now={now} approving={busy} onApprove={approve} />}

          {blocked && <ChangeNotice quote={quote} busy={busy} onRequote={requote} />}

          {(s === "USER_APPROVED" || s === "PAYPAL_ORDER_CREATED" || s === "PAYER_APPROVED") && quote.active_approval && (
            <>
              <ApprovedSnapshot approval={quote.active_approval} now={now} />
              <PaymentArea
                quoteId={quote.quote_id}
                approvalId={quote.active_approval.approval_id}
                onPaid={() => {
                  setNotice(null);
                  refresh();
                }}
                onCancelled={() => {
                  setNotice("Checkout closed. No payment was taken — your approval is still valid if you want to try again.");
                  refresh();
                }}
                onError={(e) => {
                  setError(e);
                  refresh();
                }}
                onPending={refresh}
              />
            </>
          )}

          {pending && (
            <div className="rounded-xl border border-warn/40 bg-warn-soft p-5">
              <h2 className="font-semibold text-warn">Checking payment status</h2>
              <p className="mt-1 text-sm">
                We couldn&apos;t confirm the result with PayPal yet. We won&apos;t charge you again — this page updates when the status is confirmed.
              </p>
            </div>
          )}

          {s === "PAID" && quote.payment_attempt_id && <Receipt quote={quote} attemptId={quote.payment_attempt_id} />}

          {(s === "FAILED" || s === "CANCELLED") && (
            <div className="rounded-xl border border-line bg-surface p-5">
              <h2 className="font-semibold">{s === "FAILED" ? "Payment failed" : "Purchase cancelled"}</h2>
              <p className="mt-1 text-sm text-muted">No payment was taken.</p>
              <button onClick={() => requote(quote.product.product_id)} disabled={busy} className="mt-3 rounded-lg bg-ink px-4 py-2 text-sm text-white disabled:opacity-40">
                Start again with a fresh quote
              </button>
            </div>
          )}
        </div>

        <aside className="space-y-4">
          <Timeline events={events} />
          <div className="rounded-xl border border-line bg-surface p-4 text-xs text-muted">
            <p className="font-medium text-ink">How you&apos;re protected</p>
            <ul className="mt-2 space-y-1">
              <li>• Only the exact item and total you approve can be paid.</li>
              <li>• Approval is re-checked before the PayPal order and before capture.</li>
              <li>• The AI can recommend, but it can never approve a payment.</li>
            </ul>
          </div>
        </aside>
      </div>
    </div>
  );
}

function QuoteSkeleton() {
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
      <div className="h-80 animate-pulse rounded-xl bg-surface" />
      <div className="h-60 animate-pulse rounded-xl bg-surface" />
    </div>
  );
}
