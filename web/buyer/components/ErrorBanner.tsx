"use client";
import type { ApiError } from "@/lib/errors";

/** Renders the shared error contract. Backend owners enforce; this only explains. */
export function ErrorBanner({ error, onRetry }: { error: ApiError | null; onRetry?: () => void }) {
  if (!error) return null;
  return (
    <div role="alert" className="rounded-lg border border-stop/30 bg-stop-soft p-4 text-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="font-medium text-stop">{error.message}</p>
          <p className="mt-1 font-mono text-xs text-muted">
            {error.code} · request {error.requestId}
          </p>
        </div>
        {error.retryable && onRetry && (
          <button onClick={onRetry} className="rounded-md border border-stop/40 bg-surface px-3 py-1.5 text-stop hover:bg-stop-soft">
            Try again
          </button>
        )}
      </div>
    </div>
  );
}
