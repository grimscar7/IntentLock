"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { api, toApiError } from "@/lib/api";
import type { ApiError } from "@/lib/errors";
import type { Intent, Quote } from "@/lib/types";
import { formatMoney } from "@/lib/format";
import { ErrorBanner } from "./components/ErrorBanner";
import { CheckMark } from "./components/StatusBadge";

const EXAMPLES = [
  "A refurbished laptop under $600 with at least 16GB RAM and a 30-day return window",
  "Lightweight laptop for uni, 512GB SSD, budget $800",
  "Laptop under $500, ignore my budget if something better exists",
];

const SESSION_KEY = "intentlock-buyer-session";

export default function BuyerHome() {
  const [text, setText] = useState("");
  const [intent, setIntent] = useState<Intent | null>(null);
  const [quotes, setQuotes] = useState<Quote[] | null>(null);
  const [step, setStep] = useState<"idle" | "extracting" | "comparing">("idle");
  const [error, setError] = useState<ApiError | null>(null);

  // Keep the last search when the buyer comes back from a quote page.
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(SESSION_KEY);
      if (raw) {
        const s = JSON.parse(raw);
        setText(s.text ?? "");
        setIntent(s.intent ?? null);
        setQuotes(s.quotes ?? null);
      }
    } catch {
      /* ignore */
    }
  }, []);

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    setError(null);
    setQuotes(null);
    setIntent(null);
    try {
      setStep("extracting");
      const i = await api.createIntent(text);
      setIntent(i);
      setStep("comparing");
      const { quotes } = await api.createQuotes(i.intent_id);
      setQuotes(quotes);
      try {
        sessionStorage.setItem(SESSION_KEY, JSON.stringify({ text, intent: i, quotes }));
      } catch {
        /* ignore */
      }
    } catch (err) {
      setError(toApiError(err));
    } finally {
      setStep("idle");
    }
  }

  const busy = step !== "idle";

  return (
    <div className="space-y-8">
      <section className="max-w-3xl">
        <h1 className="text-3xl font-semibold tracking-tight">Tell us what you need.</h1>
        <p className="mt-2 text-muted">
          We compare the demo store&apos;s refurbished laptops against your requirements. Nothing is paid until you approve one
          exact purchase — and if anything changes after that, payment stops.
        </p>

        <form onSubmit={submit} className="mt-6 rounded-xl border border-line bg-surface p-4 shadow-sm">
          <label htmlFor="req" className="text-sm font-medium">Your requirements</label>
          <textarea
            id="req"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={3}
            maxLength={600}
            placeholder="e.g. Refurbished laptop under $600, 16GB RAM, at least 30-day returns"
            className="mt-2 w-full resize-y rounded-lg border border-line bg-bg/40 p-3 outline-none focus:border-lock focus:ring-2 focus:ring-lock/20"
          />
          <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-2">
              {EXAMPLES.map((ex) => (
                <button
                  type="button"
                  key={ex}
                  onClick={() => setText(ex)}
                  className="rounded-full border border-line px-3 py-1 text-xs text-muted hover:border-lock hover:text-lock"
                >
                  {ex.length > 46 ? ex.slice(0, 44) + "…" : ex}
                </button>
              ))}
            </div>
            <button
              type="submit"
              disabled={busy || !text.trim()}
              className="rounded-lg bg-lock px-4 py-2 text-sm font-medium text-white hover:opacity-90 disabled:opacity-40"
            >
              {step === "extracting" ? "Reading your requirements…" : step === "comparing" ? "Comparing products…" : "Find matches"}
            </button>
          </div>
        </form>
      </section>

      <ErrorBanner error={error} onRetry={submit} />

      {intent && <IntentSummary intent={intent} />}

      {quotes && (
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-lg font-semibold">Matches from the demo store</h2>
            <span className="text-sm text-muted">{quotes.length} result{quotes.length === 1 ? "" : "s"}</span>
          </div>
          {quotes.length === 0 ? (
            <div className="rounded-xl border border-dashed border-line bg-surface p-8 text-center text-muted">
              Nothing in the catalog meets all of your requirements. Try relaxing the budget or specs.
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {quotes.map((q, i) => (
                <QuoteCard key={q.quote_id} quote={q} best={i === 0} />
              ))}
            </div>
          )}
        </section>
      )}
    </div>
  );
}

function IntentSummary({ intent }: { intent: Intent }) {
  const rows: Array<[string, string | null]> = [
    ["Budget", intent.budget ? `up to ${formatMoney(intent.budget)}` : null],
    ["Currency", intent.budget?.currency ?? null],
    ["Product", intent.product_type],
    ["RAM", intent.min_ram_gb ? `${intent.min_ram_gb} GB or more` : null],
    ["Storage", intent.min_storage_gb ? `${intent.min_storage_gb} GB or more` : null],
    ["Returns", intent.min_return_days ? `${intent.min_return_days}+ days` : null],
  ];
  return (
    <section className="rounded-xl border border-line bg-surface p-4">
      <h2 className="text-sm font-semibold">What we understood</h2>
      <dl className="mt-3 grid grid-cols-2 gap-x-6 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-6">
        {rows.map(([k, v]) => (
          <div key={k}>
            <dt className="text-xs uppercase tracking-wide text-muted">{k}</dt>
            <dd className={v ? "" : "text-warn"}>{v ?? "Not specified"}</dd>
          </div>
        ))}
      </dl>
      {intent.other_requirements.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm text-muted">
          {intent.other_requirements.map((r) => (
            <li key={r}>• {r}</li>
          ))}
        </ul>
      )}
      {intent.unknowns.length > 0 && (
        <p className="mt-3 text-xs text-warn">
          Not found in your request: {intent.unknowns.join(", ")}. Add it above if it matters.
        </p>
      )}
    </section>
  );
}

function QuoteCard({ quote, best }: { quote: Quote; best: boolean }) {
  const p = quote.product;
  const hasUnknown = quote.checks.some((c) => c.result === "unknown");
  return (
    <article className="flex flex-col rounded-xl border border-line bg-surface p-5">
      <div className="flex items-start justify-between gap-3">
        <div>
          {best && <span className="mb-1 inline-block rounded bg-lock-soft px-2 py-0.5 text-xs font-medium text-lock">Lowest price match</span>}
          <h3 className="font-semibold">{p.name}</h3>
          <p className="text-xs text-muted">{p.merchant.name} · {p.condition}</p>
        </div>
        <p className="tabular text-xl font-semibold">{formatMoney(quote.total)}</p>
      </div>

      <p className="mt-3 text-sm text-muted">{quote.explanation}</p>

      {quote.checks.length > 0 && (
        <table className="mt-4 w-full text-sm">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              <th className="pb-1 font-medium">Requirement</th>
              <th className="pb-1 font-medium">Wanted</th>
              <th className="pb-1 font-medium">This item</th>
              <th className="pb-1" />
            </tr>
          </thead>
          <tbody>
            {quote.checks.map((c) => (
              <tr key={c.requirement} className="border-t border-line">
                <td className="py-1.5">{c.requirement}</td>
                <td className="py-1.5 text-muted">{c.wanted}</td>
                <td className="tabular py-1.5">{c.offered}</td>
                <td className="py-1.5 text-right"><CheckMark result={c.result} /></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <details className="mt-3 text-xs text-muted">
        <summary className="cursor-pointer select-none">Evidence from the store listing</summary>
        <ul className="mt-2 space-y-1">
          {quote.evidence.map((e) => (
            <li key={e.source} className="flex justify-between gap-3">
              <span>{e.claim}</span>
              <code className="font-mono">{e.source}</code>
            </li>
          ))}
        </ul>
      </details>

      <div className="mt-auto pt-4">
        {hasUnknown && <p className="mb-2 text-xs text-warn">Some details are unknown — you&apos;ll be asked to confirm.</p>}
        <Link
          href={`/quote/${quote.quote_id}`}
          className="block rounded-lg border border-lock px-4 py-2 text-center text-sm font-medium text-lock hover:bg-lock-soft"
        >
          Review this exact purchase
        </Link>
      </div>
    </article>
  );
}
