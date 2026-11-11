"use client";
import type { QuoteDetail } from "@/lib/types";
import { formatMoney } from "@/lib/format";

/**
 * F8 — explains why payment was stopped and offers valid ways forward.
 * Rules are enforced by the backend; this screen only explains the result.
 */
export function ChangeNotice({
  quote,
  busy,
  onRequote,
}: {
  quote: QuoteDetail;
  busy: boolean;
  onRequote: (productId?: string) => void;
}) {
  const expired = quote.status === "EXPIRED";
  return (
    <section className="rounded-xl border-2 border-stop/60 bg-surface">
      <div className="border-b border-stop/20 bg-stop-soft px-5 py-3">
        <h2 className="font-semibold text-stop">{expired ? "This approval has expired" : "Payment stopped — the purchase changed"}</h2>
        <p className="mt-0.5 text-sm text-stop/80">
          {expired
            ? "Approvals only last a short time so an old \"yes\" can't be used later. Nothing was charged."
            : "Something material changed after you saw this quote, so your earlier approval no longer counts. Nothing was charged."}
        </p>
      </div>

      {quote.changes.length > 0 && (
        <div className="px-5 py-4">
          <h3 className="text-sm font-medium">What changed</h3>
          <table className="mt-2 w-full text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="pb-1 font-medium">Field</th>
                <th className="pb-1 font-medium">You approved</th>
                <th className="pb-1 font-medium">Now</th>
              </tr>
            </thead>
            <tbody>
              {quote.changes.map((c) => (
                <tr key={c.field} className="border-t border-line">
                  <td className="py-2">{c.label}</td>
                  <td className="tabular py-2 text-muted line-through decoration-stop/50">{c.before}</td>
                  <td className="tabular py-2 font-medium">{c.after}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="space-y-3 border-t border-line px-5 py-4">
        <button
          onClick={() => onRequote(quote.product.product_id)}
          disabled={busy}
          className="w-full rounded-lg bg-ink px-4 py-2.5 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          {busy ? "Getting a fresh quote…" : "Get a fresh quote for this item"}
        </button>
        <p className="text-xs text-muted">A fresh quote re-checks it against your requirements. You&apos;ll review and approve again.</p>

        {quote.alternatives.length > 0 && (
          <div className="pt-2">
            <h3 className="text-sm font-medium">Other items that still meet your requirements</h3>
            <ul className="mt-2 divide-y divide-line rounded-lg border border-line">
              {quote.alternatives.map((a) => (
                <li key={a.product_id} className="flex items-center justify-between gap-3 px-3 py-2 text-sm">
                  <span>
                    {a.name}
                    <span className="block text-xs text-muted">{a.merchant}</span>
                  </span>
                  <span className="flex items-center gap-3">
                    <span className="tabular font-medium">{formatMoney(a.price)}</span>
                    <button
                      onClick={() => onRequote(a.product_id)}
                      disabled={busy}
                      className="rounded-md border border-lock px-2.5 py-1 text-xs font-medium text-lock hover:bg-lock-soft disabled:opacity-40"
                    >
                      Quote this
                    </button>
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
    </section>
  );
}
