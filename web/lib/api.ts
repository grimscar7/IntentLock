/**
 * Typed client for the IntentLock backend.
 *
 * - Real mode: calls NEXT_PUBLIC_API_BASE_URL (FastAPI on Render).
 * - Mock mode: NEXT_PUBLIC_USE_MOCKS=true, or no base URL set. Uses lib/mock.ts,
 *   which follows the same contract so screens can be built before the backend is ready.
 *
 * The browser only ever sends record identifiers (intent_id, quote_id, approval_id).
 * It never sends prices, totals or payee details — the server owns those values.
 */
import type {
  ApiErrorBody,
  ApprovalSnapshot,
  AuditEvent,
  Intent,
  MerchantProduct,
  MerchantUpdate,
  PaymentAttempt,
  QuoteDetail,
  QuotesResponse,
} from "./types";
import * as mock from "./mock";
import { ApiError } from "./errors";

export { ApiError, toApiError } from "./errors";

const BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") ?? "";
export const USE_MOCKS = process.env.NEXT_PUBLIC_USE_MOCKS === "true" || BASE === "";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE}${path}`, {
      ...init,
      credentials: "include", // backend binds every quote/attempt to the authenticated user
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(
      {
        code: "NETWORK_ERROR",
        message: "Could not reach the server. Check your connection and try again.",
        retryable: true,
        request_id: "client",
      },
      0,
    );
  }
  if (!res.ok) {
    let body: ApiErrorBody;
    try {
      body = (await res.json()) as ApiErrorBody;
    } catch {
      body = {
        code: "HTTP_" + res.status,
        message: res.statusText || "Request failed",
        retryable: res.status >= 500,
        request_id: res.headers.get("x-request-id") ?? "unknown",
      };
    }
    throw new ApiError(body, res.status);
  }
  return (await res.json()) as T;
}

const post = <T>(path: string, body: unknown) =>
  request<T>(path, { method: "POST", body: JSON.stringify(body) });

export const api = {
  createIntent: (text: string): Promise<Intent> =>
    USE_MOCKS ? mock.createIntent(text) : post("/api/intents", { text }),

  createQuotes: (intent_id: string, product_id?: string): Promise<QuotesResponse> =>
    USE_MOCKS ? mock.createQuotes(intent_id, product_id) : post("/api/quotes", { intent_id, product_id }),

  getQuote: (quote_id: string): Promise<QuoteDetail> =>
    USE_MOCKS ? mock.getQuote(quote_id) : request(`/api/quotes/${encodeURIComponent(quote_id)}`),

  getQuoteEvents: (quote_id: string): Promise<AuditEvent[]> =>
    USE_MOCKS ? mock.getQuoteEvents(quote_id) : request(`/api/quotes/${encodeURIComponent(quote_id)}/events`),

  approve: (quote_id: string): Promise<ApprovalSnapshot> =>
    USE_MOCKS ? mock.approve(quote_id) : post("/api/approvals", { quote_id }),

  /** Read-only. Creating/capturing payments is Hari's component (web/payments). */
  getPayment: (attempt_id: string): Promise<PaymentAttempt> =>
    USE_MOCKS ? mock.getPayment(attempt_id) : request(`/api/payments/${encodeURIComponent(attempt_id)}`),

  // Merchant panel — ASSUMED endpoints, confirm with Vishnu.
  listMerchantProducts: (): Promise<MerchantProduct[]> =>
    USE_MOCKS ? mock.listMerchantProducts() : request("/api/merchant/products"),

  updateMerchantProduct: (product_id: string, update: MerchantUpdate): Promise<MerchantProduct> =>
    USE_MOCKS
      ? mock.updateMerchantProduct(product_id, update)
      : request(`/api/merchant/products/${encodeURIComponent(product_id)}`, {
          method: "PATCH",
          body: JSON.stringify(update),
        }),

  resetDemo: (): Promise<void> => (USE_MOCKS ? mock.resetDemo() : post("/api/merchant/reset", {})),
};
