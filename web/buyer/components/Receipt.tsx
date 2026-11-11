"use client";
import { useEffect, useState } from "react";
import { api, toApiError } from "@/lib/api";
import type { ApiError } from "@/lib/errors";
import type { PaymentAttempt, QuoteDetail } from "@/lib/types";
import { formatDateTime, formatMoney } from "@/lib/format";
import { ErrorBanner } from "./ErrorBanner";

/** F9 — verified receipt. Only rendered from the server's persisted payment result. */
export function Receipt({ quote, attemptId }: { quote: QuoteDetail; attemptId: string }) {
  const [payment, setPayment] = useState<PaymentAttempt | null>(null);
  const [error, setError] = useState<ApiError | null>(null);

  useEffect(() => {
    api.getPayment(attemptId).then(setPayment).catch((e) => setError(toApiError(e)));
  }, [attemptId]);

  if (error) return <ErrorBanner error={error} />;
  if (!payment) return <div className="h-64 animate-pulse rounded-xl bg-surface" />;

  const verified = payment.state === "PAID" && !!payment.capture_id;
  const amountMatches =
    payment.amount.amount_minor === quote.total.amount_minor && payment.amount.currency === quote.total.currency;

  return (
    <section className="overflow-hidden rounded-xl border-2 border-lock bg-surface">
      <div className="bg-lock px-5 py-4 text-white">
        <p className="text-sm opacity-80">Receipt</p>
        <p className="tabular text-3xl font-semibold">{formatMoney(payment.amount)}</p>
        <p className="mt-1 text-sm">{verified ? "Payment captured and verified" : "Payment not yet verified"}</p>
      </div>
      <dl className="grid grid-cols-[9rem_1fr] gap-y-2 px-5 py-4 text-sm">
        <dt className="text-muted">Paid to</dt>
        <dd>{payment.merchant_name}</dd>
        <dt className="text-muted">Item</dt>
        <dd>{quote.product.name}</dd>
        <dt className="text-muted">Variant</dt>
        <dd><code className="font-mono text-xs">{quote.product.variant_id} (v{quote.product.version})</code></dd>
        <dt className="text-muted">Returns</dt>
        <dd>{quote.product.return_policy.days === null ? "Not stated" : `${quote.product.return_policy.days} days`}</dd>
        <dt className="text-muted">Paid at</dt>
        <dd>{payment.captured_at ? formatDateTime(payment.captured_at) : "—"}</dd>
        <dt className="text-muted">PayPal order</dt>
        <dd><code className="font-mono text-xs">{payment.paypal_order_id ?? "—"}</code></dd>
        <dt className="text-muted">Capture ID</dt>
        <dd><code className="font-mono text-xs">{payment.capture_id ?? "—"}</code></dd>
        <dt className="text-muted">Approval</dt>
        <dd><code className="font-mono text-xs">{payment.approval_id}</code></dd>
      </dl>
      <div className={`border-t px-5 py-3 text-sm ${amountMatches ? "border-line text-lock" : "border-stop/30 bg-stop-soft text-stop"}`}>
        {amountMatches ? "✓ Amount charged matches the amount you approved." : "Amount charged does not match the approved quote — contact support."}
      </div>
      <div className="border-t border-line px-5 py-3 text-xs text-muted">PayPal Sandbox test transaction — no real money moved.</div>
    </section>
  );
}
