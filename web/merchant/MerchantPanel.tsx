"use client";
import { useCallback, useEffect, useState } from "react";
import { api, toApiError } from "@/lib/api";
import type { ApiError } from "@/lib/errors";
import type { MerchantProduct, MerchantUpdate } from "@/lib/types";
import { formatMoney } from "@/lib/format";
import { ErrorBanner } from "@/buyer/components/ErrorBanner";

/**
 * F5 — controlled merchant panel for the demo. Every save creates a new product version,
 * which the backend uses to invalidate approvals made against the old version.
 */
export default function MerchantPanel() {
  const [products, setProducts] = useState<MerchantProduct[] | null>(null);
  const [error, setError] = useState<ApiError | null>(null);
  const [saved, setSaved] = useState<string | null>(null);

  const load = useCallback(async () => {
    try {
      setProducts(await api.listMerchantProducts());
      setError(null);
    } catch (e) {
      setError(toApiError(e));
    }
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  async function save(p: MerchantProduct, update: MerchantUpdate) {
    setError(null);
    try {
      const updated = await api.updateMerchantProduct(p.product_id, update);
      setProducts((list) => list?.map((x) => (x.product_id === updated.product_id ? updated : x)) ?? null);
      setSaved(`${updated.name} saved as version ${updated.version}`);
      setTimeout(() => setSaved(null), 3000);
    } catch (e) {
      setError(toApiError(e));
    }
  }

  async function reset() {
    if (!confirm("Reset the demo store? This clears all quotes, approvals and payments in mock mode.")) return;
    await api.resetDemo();
    try {
      sessionStorage.clear();
    } catch {
      /* ignore */
    }
    await load();
    setSaved("Demo store reset");
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Merchant panel</h1>
          <p className="mt-1 max-w-2xl text-sm text-muted">
            Change a product after a buyer has approved it to see IntentLock block payment. Open the buyer&apos;s quote in another tab — it
            updates within a few seconds.
          </p>
        </div>
        <button onClick={reset} className="rounded-lg border border-line bg-surface px-3 py-2 text-sm hover:bg-bg">
          Reset demo
        </button>
      </div>

      <ErrorBanner error={error} onRetry={load} />
      {saved && (
        <div role="status" className="rounded-lg bg-lock-soft px-4 py-2 text-sm text-lock">
          {saved}
        </div>
      )}

      {!products ? (
        <div className="h-64 animate-pulse rounded-xl bg-surface" />
      ) : (
        <div className="space-y-3">
          {products.map((p) => (
            <ProductRow key={`${p.product_id}-${p.version}`} product={p} onSave={(u) => save(p, u)} />
          ))}
        </div>
      )}
    </div>
  );
}

function ProductRow({ product: p, onSave }: { product: MerchantProduct; onSave: (u: MerchantUpdate) => Promise<void> }) {
  const [price, setPrice] = useState((p.price.amount_minor / 100).toFixed(2));
  const [currency, setCurrency] = useState(p.price.currency);
  const [variant, setVariant] = useState(p.variant_id);
  const [returns, setReturns] = useState(p.return_policy.days === null ? "" : String(p.return_policy.days));
  const [saving, setSaving] = useState(false);

  const priceMinor = Math.round(parseFloat(price) * 100);
  const returnDays = returns.trim() === "" ? null : parseInt(returns, 10);
  const dirty =
    priceMinor !== p.price.amount_minor || currency !== p.price.currency || variant !== p.variant_id || returnDays !== p.return_policy.days;
  const valid = Number.isFinite(priceMinor) && priceMinor > 0 && (returnDays === null || (Number.isInteger(returnDays) && returnDays >= 0));

  async function submit(u: MerchantUpdate) {
    setSaving(true);
    await onSave(u);
    setSaving(false);
  }

  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div>
          <h2 className="font-medium">{p.name}</h2>
          <p className="text-xs text-muted">
            {p.specs.cpu} · {p.specs.ram} · {p.specs.storage} · {p.condition}
          </p>
        </div>
        <span className="rounded bg-bg px-2 py-0.5 font-mono text-xs text-muted">
          v{p.version} · {formatMoney(p.price)}
        </span>
      </div>

      <div className="mt-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-[9rem_6rem_1fr_8rem_auto]">
        <Field label="Price">
          <input type="number" min="0.01" step="0.01" value={price} onChange={(e) => setPrice(e.target.value)} className={inputCls} />
        </Field>
        <Field label="Currency">
          <select value={currency} onChange={(e) => setCurrency(e.target.value)} className={inputCls}>
            {["USD", "AUD", "EUR", "INR"].map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </Field>
        <Field label="Variant shipped">
          <select value={variant} onChange={(e) => setVariant(e.target.value)} className={inputCls}>
            {p.alternate_variants.map((v) => (
              <option key={v.variant_id} value={v.variant_id}>
                {v.label}
              </option>
            ))}
          </select>
        </Field>
        <Field label="Return days">
          <input type="number" min="0" placeholder="none" value={returns} onChange={(e) => setReturns(e.target.value)} className={inputCls} />
        </Field>
        <div className="flex items-end">
          <button
            onClick={() => submit({ price_minor: priceMinor, currency, variant_id: variant, return_days: returnDays })}
            disabled={!dirty || !valid || saving}
            className="w-full rounded-lg bg-ink px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-30"
          >
            {saving ? "Saving…" : "Save"}
          </button>
        </div>
      </div>

      <div className="mt-3 flex flex-wrap gap-2 text-xs">
        <span className="py-1 text-muted">Quick demo changes:</span>
        <Quick onClick={() => submit({ price_minor: p.price.amount_minor + 5000 })} disabled={saving}>Raise price $50</Quick>
        {p.alternate_variants.length > 1 && (
          <Quick
            onClick={() => submit({ variant_id: p.alternate_variants.find((v) => v.variant_id !== p.variant_id)!.variant_id })}
            disabled={saving}
          >
            Swap variant (same price)
          </Quick>
        )}
        <Quick onClick={() => submit({ return_days: 7 })} disabled={saving || p.return_policy.days === 7}>Cut returns to 7 days</Quick>
        <Quick onClick={() => submit({ currency: p.price.currency === "USD" ? "AUD" : "USD" })} disabled={saving}>Switch currency</Quick>
      </div>
    </div>
  );
}

const inputCls =
  "w-full rounded-md border border-line bg-surface px-2.5 py-1.5 text-sm outline-none focus:border-lock focus:ring-2 focus:ring-lock/20";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block">
      <span className="mb-1 block text-xs text-muted">{label}</span>
      {children}
    </label>
  );
}

function Quick({ children, ...props }: React.ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button {...props} className="rounded-full border border-line px-2.5 py-1 hover:border-warn hover:text-warn disabled:opacity-30">
      {children}
    </button>
  );
}
