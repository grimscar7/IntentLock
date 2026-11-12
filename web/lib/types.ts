/**
 * Frontend view of the API contract agreed in the IntentLock execution plan (section 4).
 *
 * Backend owners: Vishnu (intents, quotes, approvals, events, merchant catalog)
 *                 Payments (PayPal integration)
 * If a backend shape differs from what is here, update THIS file first —
 * every screen reads its types from here.
 *
 * Money is always integer minor units (cents) plus an ISO currency code.
 */

export type Money = { amount_minor: number; currency: string };

/** Shared error contract: code, message, retryable, request_id */
export type ApiErrorBody = {
  code: string; // e.g. APPROVAL_EXPIRED
  message: string;
  retryable: boolean;
  request_id: string;
};

/** "unknown" is used when evidence is missing — never silently treated as satisfied. */
export type Check = "pass" | "fail" | "unknown";

// ---------- POST /api/intents ----------
export type IntentRequest = { text: string };

export type Intent = {
  intent_id: string;
  raw_text: string;
  budget: Money | null;
  product_type: string | null;
  min_ram_gb: number | null;
  min_storage_gb: number | null;
  min_return_days: number | null;
  other_requirements: string[];
  /** Fields the AI could not determine from the buyer's text. */
  unknowns: string[];
};

// ---------- Catalog / product versions ----------
export type ReturnPolicy = {
  days: number | null; // null = unknown / no evidence
  restocking_fee_pct: number | null;
  policy_version: string;
};

export type ProductVersion = {
  product_id: string;
  variant_id: string;
  version: number;
  name: string;
  merchant: { merchant_id: string; name: string };
  price: Money;
  specs: Record<string, string>;
  condition: string;
  return_policy: ReturnPolicy;
};

// ---------- POST /api/quotes ----------
export type QuoteRequest = { intent_id: string; product_id?: string };

export type Evidence = {
  claim: string;
  source: string; // field reference in the catalog record, e.g. "specs.ram"
};

export type RequirementCheck = {
  requirement: string; // "Budget", "RAM", "Return window", ...
  wanted: string;
  offered: string;
  result: Check;
};

export type QuoteStatus =
  | "DRAFT"
  | "QUOTED"
  | "USER_APPROVED"
  | "PAYPAL_ORDER_CREATED"
  | "PAYER_APPROVED"
  | "CAPTURE_IN_PROGRESS"
  | "PAID"
  | "REAPPROVAL_REQUIRED"
  | "EXPIRED"
  | "CANCELLED"
  | "FAILED"
  | "PAYMENT_STATUS_UNKNOWN"
  | "RECONCILE";

export type Quote = {
  quote_id: string;
  intent_id: string;
  product: ProductVersion;
  quantity: number;
  total: Money;
  expires_at: string; // ISO
  status: QuoteStatus;
  explanation: string;
  evidence: Evidence[];
  checks: RequirementCheck[];
};

export type QuotesResponse = { quotes: Quote[] };

// ---------- POST /api/approvals ----------
export type ApprovalRequest = { quote_id: string };

export type ApprovalSnapshot = {
  approval_id: string;
  quote_id: string;
  approved_fields: {
    merchant_name: string;
    product_name: string;
    variant_id: string;
    product_version: number;
    quantity: number;
    total: Money;
    return_days: number | null;
    policy_version: string;
  };
  approved_at: string;
  expires_at: string;
  status: "ACTIVE" | "INVALIDATED" | "EXPIRED" | "USED";
};

// ---------- GET /api/quotes/{quote_id} ----------
export type FieldChange = {
  field: string; // "price", "variant", "currency", "merchant", "return_policy"
  label: string;
  before: string;
  after: string;
};

export type QuoteDetail = Quote & {
  active_approval: ApprovalSnapshot | null;
  /** Material changes since the latest approval (empty when none). */
  changes: FieldChange[];
  requires_new_approval: boolean;
  /** Other catalog products that still meet the buyer's requirements. */
  alternatives: Array<{ product_id: string; name: string; price: Money; merchant: string }>;
  /** Set once capture handler has persisted a verified result. */
  payment_attempt_id: string | null;
};

// ---------- GET /api/quotes/{quote_id}/events ----------
export type AuditEvent = {
  event_id: string;
  actor: "buyer" | "merchant" | "system" | "ai" | "paypal";
  event_type: string;
  timestamp: string;
  details: string; // already redacted by backend
};

// ---------- GET /api/payments/{attempt_id} ----------
export type PaymentAttempt = {
  attempt_id: string;
  approval_id: string;
  state: QuoteStatus;
  paypal_order_id: string | null;
  capture_id: string | null;
  amount: Money;
  merchant_name: string;
  captured_at: string | null;
};

// ---------- Merchant panel (ASSUMED — not yet in the plan's contract; confirm with Vishnu) ----------
export type MerchantProduct = ProductVersion & {
  alternate_variants: Array<{ variant_id: string; label: string }>;
};

export type MerchantUpdate = {
  price_minor?: number;
  currency?: string;
  variant_id?: string;
  return_days?: number | null;
};
