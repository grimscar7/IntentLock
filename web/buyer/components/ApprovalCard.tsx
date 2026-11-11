"use client";
import { useState } from "react";
import type { ApprovalSnapshot, QuoteDetail } from "@/lib/types";
import { formatMoney, remaining } from "@/lib/format";
import { CheckMark } from "./StatusBadge";

/**
 * F3 — the exact-purchase approval card.
 * Shows every material field the backend will lock: merchant, item/variant, quantity,
 * total, currency, return terms and expiry.
 */
export function ApprovalCard({
  quote,
  now,
  approving,
  onApprove,
}: {
  quote: QuoteDetail;
  now: number;
  approving: boolean;
  onApprove: () => void;
}) {
  const p = quote.product;
  const unknown = quote.checks.filter((c) => c.result === "unknown");
  const failing = quote.checks.filter((c) => c.result === "fail");
  const [ackUnknown, setAckUnknown] = useState(false);
  const left = remaining(quote.expires_at, now);
  const canApprove = quote.status === "QUOTED" && !!left && failing.length === 0 && (unknown.length === 0 || ackUnknown);

  return (
    <section className="rounded-xl border-2 border-ink bg-surface">
      <div className="flex items-center justify-between border-b border-line px-5 py-3">
        <h2 className="font-semibold">You are approving exactly this</h2>
        <span className="tabular text-xs text-muted">{left ? `Quote valid for ${left}` : "Quote expired"}</span>
      </div>

      <LockedFields
        rows={[
          ["Merchant", p.merchant.name],
          ["Item", p.name],
          ["Variant", `${p.specs.ram} · ${p.specs.storage} · ${p.condition}`],
          ["Variant ID", <code key="v" className="font-mono text-xs">{p.variant_id} (v{p.version})</code>],
          ["Quantity", String(quote.quantity)],
          ["Returns", p.return_policy.days === null ? <span key="r" className="text-warn">Not stated by store</span> : `${p.return_policy.days} days`],
        ]}
        total={formatMoney(quote.total)}
        currency={quote.total.currency}
      />

      {quote.checks.length > 0 && (
        <div className="border-t border-line px-5 py-3 text-sm">
          <ul className="flex flex-wrap gap-x-5 gap-y-1">
            {quote.checks.map((c) => (
              <li key={c.requirement} className="flex items-center gap-1.5">
                <CheckMark result={c.result} /> {c.requirement}: <span className="text-muted">{c.offered}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="space-y-3 border-t border-line px-5 py-4">
        {failing.length > 0 && (
          <p className="text-sm text-stop">This item does not meet: {failing.map((f) => f.requirement).join(", ")}. It can&apos;t be approved.</p>
        )}
        {unknown.length > 0 && failing.length === 0 && (
          <label className="flex items-start gap-2 text-sm">
            <input type="checkbox" checked={ackUnknown} onChange={(e) => setAckUnknown(e.target.checked)} className="mt-1 accent-lock" />
            <span>
              The store gives no evidence for <strong>{unknown.map((u) => u.requirement.toLowerCase()).join(", ")}</strong>. I understand this
              is unknown and still want to approve.
            </span>
          </label>
        )}
        <button
          onClick={onApprove}
          disabled={!canApprove || approving}
          className="w-full rounded-lg bg-lock px-4 py-3 font-medium text-white hover:opacity-90 disabled:opacity-40"
        >
          {approving ? "Locking approval…" : `Approve ${formatMoney(quote.total)} purchase`}
        </button>
        <p className="text-center text-xs text-muted">
          Approval lasts 10 minutes. If the price, item, currency, merchant or return terms change, payment is stopped and you&apos;ll be asked again.
        </p>
      </div>
    </section>
  );
}

/** Read-only view of an approval that is now locked. */
export function ApprovedSnapshot({ approval, now }: { approval: ApprovalSnapshot; now: number }) {
  const a = approval.approved_fields;
  const left = remaining(approval.expires_at, now);
  return (
    <section className="rounded-xl border-2 border-lock bg-surface">
      <div className="flex items-center justify-between border-b border-lock/20 bg-lock-soft px-5 py-3">
        <h2 className="flex items-center gap-2 font-semibold text-lock">
          <svg width="16" height="16" viewBox="0 0 24 24" aria-hidden>
            <rect x="4" y="10" width="16" height="11" rx="2" fill="currentColor" />
            <path d="M8 10V7a4 4 0 0 1 8 0v3" stroke="currentColor" strokeWidth="2" fill="none" />
          </svg>
          Approval locked
        </h2>
        <span className="tabular text-xs text-lock">{left ? `Pay within ${left}` : "Approval expired"}</span>
      </div>
      <LockedFields
        rows={[
          ["Merchant", a.merchant_name],
          ["Item", a.product_name],
          ["Variant ID", <code key="v" className="font-mono text-xs">{a.variant_id} (v{a.product_version})</code>],
          ["Quantity", String(a.quantity)],
          ["Returns", a.return_days === null ? "Not stated" : `${a.return_days} days`],
          ["Approval ID", <code key="a" className="font-mono text-xs">{approval.approval_id}</code>],
        ]}
        total={formatMoney(a.total)}
        currency={a.total.currency}
      />
    </section>
  );
}

function LockedFields({ rows, total, currency }: { rows: Array<[string, React.ReactNode]>; total: string; currency: string }) {
  return (
    <div className="grid gap-4 px-5 py-4 sm:grid-cols-[1fr_auto]">
      <dl className="grid grid-cols-[7rem_1fr] gap-y-1.5 text-sm">
        {rows.map(([k, v]) => (
          <div key={k} className="contents">
            <dt className="text-muted">{k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div className="text-left sm:text-right">
        <p className="text-xs uppercase tracking-wide text-muted">Total</p>
        <p className="tabular text-3xl font-semibold">{total}</p>
        <p className="text-xs text-muted">{currency}</p>
      </div>
    </div>
  );
}
