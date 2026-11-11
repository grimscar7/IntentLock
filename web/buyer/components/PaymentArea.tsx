"use client";
import { useState } from "react";
import { USE_MOCKS } from "@/lib/api";
import { devSimulatePayment } from "@/lib/mock";
import { toApiError, type ApiError } from "@/lib/errors";

/**
 * Page layout for Hari's PayPal checkout component (web/payments — owned by Hari).
 *
 * Sabari owns this frame; Hari owns everything that talks to PayPal.
 * To plug in the real checkout, Hari renders his component where <CheckoutSlot> is,
 * using these props:
 */
export type CheckoutComponentProps = {
  quoteId: string;
  approvalId: string;
  /** Call after a VERIFIED capture has been persisted server-side. */
  onPaid: (attemptId: string) => void;
  /** Buyer closed or cancelled PayPal checkout. No success state is shown. */
  onCancelled: () => void;
  /** Any shared-contract error (APPROVAL_EXPIRED, APPROVAL_INVALIDATED, INSTRUMENT_DECLINED, …). */
  onError: (error: ApiError) => void;
  /** Payment outcome unknown (e.g. capture timeout) — UI shows "checking", never "paid". */
  onPending?: () => void;
};

export function PaymentArea(props: CheckoutComponentProps) {
  return (
    <section className="rounded-xl border border-line bg-surface p-5">
      <h2 className="font-semibold">Pay with PayPal Sandbox</h2>
      <p className="mt-1 text-sm text-muted">
        The server re-checks your approval before creating the PayPal order and again before taking payment.
      </p>
      <div id="paypal-checkout-slot" className="mt-4 min-h-24">
        <CheckoutSlot {...props} />
      </div>
    </section>
  );
}

/**
 * >>> HARI: replace the body of this function with your PayPal component, e.g.
 *     return <PayPalCheckout {...props} />;
 * (import it from "@/payments/PayPalCheckout").
 */
function CheckoutSlot(props: CheckoutComponentProps) {
  if (USE_MOCKS) return <MockPaymentStub {...props} />;
  return (
    <div className="rounded-lg border border-dashed border-line p-4 text-center text-sm text-muted">
      PayPal checkout component goes here (web/payments).
    </div>
  );
}

/** DEV ONLY: lets the receipt, cancel and change-blocked screens be demoed without PayPal. */
function MockPaymentStub({ quoteId, onPaid, onCancelled, onError }: CheckoutComponentProps) {
  const [busy, setBusy] = useState(false);
  async function run(outcome: "approve" | "cancel") {
    setBusy(true);
    try {
      const attemptId = await devSimulatePayment(quoteId, outcome);
      if (attemptId) onPaid(attemptId);
      else onCancelled();
    } catch (e) {
      onError(toApiError(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="rounded-lg border border-dashed border-warn/50 bg-warn-soft/50 p-4">
      <p className="mb-3 text-xs font-medium text-warn">Mock checkout — stands in for Hari&apos;s PayPal component</p>
      <div className="flex flex-wrap gap-2">
        <button
          onClick={() => run("approve")}
          disabled={busy}
          className="flex-1 rounded-full bg-[#ffc439] px-4 py-2.5 text-sm font-semibold text-[#111] hover:brightness-95 disabled:opacity-50"
        >
          {busy ? "Processing…" : "Approve in PayPal (mock)"}
        </button>
        <button
          onClick={() => run("cancel")}
          disabled={busy}
          className="rounded-full border border-line bg-surface px-4 py-2.5 text-sm hover:bg-bg disabled:opacity-50"
        >
          Close checkout
        </button>
      </div>
    </div>
  );
}
