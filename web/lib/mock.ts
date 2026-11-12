/**
 * In-browser mock of the IntentLock backend, following the contract in lib/types.ts.
 * Data lives in localStorage so the merchant panel (one tab) can change a product
 * and the buyer page (another tab) sees it.
 *
 * FRONTEND DEVELOPMENT ONLY. The real rules live in Vishnu's policy engine and
 * the payment handlers; nothing here is a security boundary.
 */
import type {
  ApprovalSnapshot,
  AuditEvent,
  Check,
  FieldChange,
  Intent,
  MerchantProduct,
  MerchantUpdate,
  PaymentAttempt,
  ProductVersion,
  Quote,
  QuoteDetail,
  QuotesResponse,
  RequirementCheck,
} from "./types";
import { ApiError } from "./errors";
import { formatMoney } from "./format";

const KEY = "intentlock-mock-db-v1";
const QUOTE_TTL_MS = 15 * 60 * 1000;
const APPROVAL_TTL_MS = 10 * 60 * 1000;

type Db = {
  products: MerchantProduct[];
  intents: Intent[];
  quotes: Quote[];
  approvals: ApprovalSnapshot[];
  events: Array<AuditEvent & { quote_id: string }>;
  payments: PaymentAttempt[];
  quotePayments: Record<string, string>; // quote_id -> attempt_id
  seq: number;
};

const merchant = { merchant_id: "m_demo", name: "ReBoot Refurbished (Demo Store)" };

function seedProducts(): MerchantProduct[] {
  const p = (
    product_id: string,
    name: string,
    price: number,
    ram: number,
    storage: number,
    cpu: string,
    screen: string,
    condition: string,
    returnDays: number | null,
    variants: Array<{ variant_id: string; label: string }>,
  ): MerchantProduct => ({
    product_id,
    variant_id: variants[0].variant_id,
    version: 1,
    name,
    merchant,
    price: { amount_minor: price * 100, currency: "USD" },
    specs: { cpu, ram: `${ram} GB`, storage: `${storage} GB SSD`, screen },
    condition,
    return_policy: { days: returnDays, restocking_fee_pct: returnDays ? 0 : null, policy_version: "rp-1" },
    alternate_variants: variants,
  });
  return [
    p("lt_thinkpad_t14", "Lenovo ThinkPad T14 Gen 2", 549, 16, 512, "Intel Core i5-1135G7", '14" FHD', "Grade A", 30,
      [{ variant_id: "t14-16-512-blk", label: "16 GB / 512 GB, Black" }, { variant_id: "t14-8-256-blk", label: "8 GB / 256 GB, Black" }]),
    p("lt_dell_5420", "Dell Latitude 5420", 479, 16, 256, "Intel Core i5-1145G7", '14" FHD', "Grade B", 14,
      [{ variant_id: "5420-16-256", label: "16 GB / 256 GB" }, { variant_id: "5420-8-256", label: "8 GB / 256 GB" }]),
    p("lt_macbook_air_m1", "Apple MacBook Air M1", 699, 8, 256, "Apple M1", '13.3" Retina', "Grade A", 30,
      [{ variant_id: "mba-m1-8-256-gold", label: "8 GB / 256 GB, Gold" }, { variant_id: "mba-m1-8-256-grey", label: "8 GB / 256 GB, Space Grey" }]),
    p("lt_hp_840_g8", "HP EliteBook 840 G8", 589, 16, 512, "Intel Core i7-1165G7", '14" FHD', "Grade A", 30,
      [{ variant_id: "840g8-16-512", label: "16 GB / 512 GB" }, { variant_id: "840g8-8-256", label: "8 GB / 256 GB" }]),
    p("lt_xps_13", "Dell XPS 13 9310", 749, 16, 512, "Intel Core i7-1185G7", '13.4" FHD+', "Grade A", 30,
      [{ variant_id: "xps-16-512", label: "16 GB / 512 GB" }, { variant_id: "xps-8-256", label: "8 GB / 256 GB" }]),
    p("lt_surface_4", "Microsoft Surface Laptop 4", 529, 8, 256, "AMD Ryzen 5 4680U", '13.5" PixelSense', "Grade B", null,
      [{ variant_id: "sl4-8-256", label: "8 GB / 256 GB, Platinum" }]),
    p("lt_acer_swift_3", "Acer Swift 3 SF314", 399, 8, 512, "AMD Ryzen 5 5500U", '14" FHD', "Grade B", 7,
      [{ variant_id: "swift3-8-512", label: "8 GB / 512 GB" }]),
  ];
}

function emptyDb(): Db {
  return { products: seedProducts(), intents: [], quotes: [], approvals: [], events: [], payments: [], quotePayments: {}, seq: 1 };
}

function load(): Db {
  if (typeof window === "undefined") return emptyDb();
  try {
    const raw = window.localStorage.getItem(KEY);
    if (raw) return JSON.parse(raw) as Db;
  } catch {
    /* ignore and reseed */
  }
  const db = emptyDb();
  save(db);
  return db;
}

function save(db: Db) {
  try {
    window.localStorage.setItem(KEY, JSON.stringify(db));
  } catch {
    /* storage unavailable: state lives for this call only */
  }
}

const delay = (ms = 350) => new Promise((r) => setTimeout(r, ms));
const now = () => new Date().toISOString();
const id = (db: Db, prefix: string) => `${prefix}_${(db.seq++).toString().padStart(4, "0")}`;

function fail(code: string, message: string, retryable = false, status = 409): never {
  throw new ApiError({ code, message, retryable, request_id: "mock-" + Math.random().toString(36).slice(2, 8) }, status);
}

function log(db: Db, quote_id: string, actor: AuditEvent["actor"], event_type: string, details: string) {
  db.events.push({ event_id: id(db, "evt"), quote_id, actor, event_type, timestamp: now(), details });
}

function toVersion(p: MerchantProduct): ProductVersion {
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  const { alternate_variants, ...v } = p;
  return structuredClone(v);
}

function variantLabel(p: MerchantProduct, variant_id: string) {
  return p.alternate_variants.find((v) => v.variant_id === variant_id)?.label ?? variant_id;
}

// ---------------- Intents ----------------

function parseIntent(text: string): Omit<Intent, "intent_id"> {
  const t = text.toLowerCase();
  const unknowns: string[] = [];
  const other: string[] = [];

  let currency = "USD";
  if (/\baud\b|a\$|australian/.test(t)) currency = "AUD";
  else if (/\binr\b|₹|rupee/.test(t)) currency = "INR";
  else if (/\beur\b|€/.test(t)) currency = "EUR";

  const budgetMatch =
    t.match(/(?:under|below|less than|max(?:imum)?|budget(?: of| is)?|up to|<)\s*(?:a\$|us\$|\$|₹|€)?\s*(\d[\d,]*)/) ??
    t.match(/(?:a\$|us\$|\$|₹|€)\s*(\d[\d,]*)/);
  const budget = budgetMatch
    ? { amount_minor: parseInt(budgetMatch[1].replace(/,/g, ""), 10) * 100, currency }
    : null;
  if (!budget) unknowns.push("budget");

  const ram = t.match(/(\d+)\s*gb\s*(?:of\s*)?ram/) ?? t.match(/ram\s*(?:of\s*)?(\d+)\s*gb/);
  const storageMatch = t.match(/(\d+)\s*(gb|tb)\s*(?:ssd|storage|disk)/);
  const ret = t.match(/(\d+)[\s-]*day/);

  if (/ignore|override|disregard/.test(t)) {
    other.push("Instruction to override rules was noted and NOT applied — budget and policy checks always run.");
  }
  if (/light|portable|travel/.test(t)) other.push("Prefers a lightweight / portable machine");
  if (/battery/.test(t)) other.push("Good battery life");

  return {
    raw_text: text,
    budget,
    product_type: /laptop|notebook|macbook|thinkpad/.test(t) ? "laptop" : "laptop",
    min_ram_gb: ram ? parseInt(ram[1], 10) : null,
    min_storage_gb: storageMatch ? parseInt(storageMatch[1], 10) * (storageMatch[2] === "tb" ? 1024 : 1) : null,
    min_return_days: ret ? parseInt(ret[1], 10) : null,
    other_requirements: other,
    unknowns,
  };
}

export async function createIntent(text: string): Promise<Intent> {
  await delay(600);
  if (!text.trim()) fail("INTENT_EMPTY", "Describe what you want to buy.", false, 422);
  const db = load();
  const intent: Intent = { intent_id: id(db, "int"), ...parseIntent(text) };
  db.intents.push(intent);
  save(db);
  return intent;
}

// ---------------- Quotes ----------------

function evaluate(intent: Intent, p: ProductVersion): RequirementCheck[] {
  const checks: RequirementCheck[] = [];
  if (intent.budget) {
    const sameCurrency = intent.budget.currency === p.price.currency;
    checks.push({
      requirement: "Budget",
      wanted: `≤ ${formatMoney(intent.budget)}`,
      offered: formatMoney(p.price),
      result: !sameCurrency ? "fail" : p.price.amount_minor <= intent.budget.amount_minor ? "pass" : "fail",
    });
  }
  const ram = parseInt(p.specs.ram, 10);
  if (intent.min_ram_gb) {
    checks.push({ requirement: "RAM", wanted: `≥ ${intent.min_ram_gb} GB`, offered: p.specs.ram, result: ram >= intent.min_ram_gb ? "pass" : "fail" });
  }
  const storage = parseInt(p.specs.storage, 10);
  if (intent.min_storage_gb) {
    checks.push({ requirement: "Storage", wanted: `≥ ${intent.min_storage_gb} GB`, offered: p.specs.storage, result: storage >= intent.min_storage_gb ? "pass" : "fail" });
  }
  if (intent.min_return_days) {
    const d = p.return_policy.days;
    const result: Check = d === null ? "unknown" : d >= intent.min_return_days ? "pass" : "fail";
    checks.push({ requirement: "Return window", wanted: `≥ ${intent.min_return_days} days`, offered: d === null ? "Not stated" : `${d} days`, result });
  }
  return checks;
}

function explain(p: ProductVersion, checks: RequirementCheck[]) {
  const passes = checks.filter((c) => c.result === "pass").map((c) => c.requirement.toLowerCase());
  const unknown = checks.filter((c) => c.result === "unknown").map((c) => c.requirement.toLowerCase());
  let s = `${p.name} (${p.condition}) with ${p.specs.cpu}, ${p.specs.ram} RAM and ${p.specs.storage}.`;
  if (passes.length) s += ` Meets your ${passes.join(", ")} requirement${passes.length > 1 ? "s" : ""}.`;
  if (unknown.length) s += ` The store gives no evidence for ${unknown.join(", ")}, so that is marked unknown.`;
  return s;
}

function buildQuote(db: Db, intent: Intent, product: MerchantProduct): Quote {
  const v = toVersion(product);
  const checks = evaluate(intent, v);
  return {
    quote_id: id(db, "q"),
    intent_id: intent.intent_id,
    product: v,
    quantity: 1,
    total: { ...v.price },
    expires_at: new Date(Date.now() + QUOTE_TTL_MS).toISOString(),
    status: "QUOTED",
    explanation: explain(v, checks),
    evidence: [
      { claim: `CPU: ${v.specs.cpu}`, source: "specs.cpu" },
      { claim: `RAM: ${v.specs.ram}`, source: "specs.ram" },
      { claim: `Storage: ${v.specs.storage}`, source: "specs.storage" },
      { claim: `Price: ${formatMoney(v.price)}`, source: `price@v${v.version}` },
      {
        claim: v.return_policy.days === null ? "Return policy: not stated by store" : `Returns: ${v.return_policy.days} days`,
        source: `return_policy@${v.return_policy.policy_version}`,
      },
    ],
    checks,
  };
}

export async function createQuotes(intent_id: string, product_id?: string): Promise<QuotesResponse> {
  await delay(800);
  const db = load();
  const intent = db.intents.find((i) => i.intent_id === intent_id);
  if (!intent) fail("INTENT_NOT_FOUND", "That request has expired. Please describe it again.", false, 404);

  let products = db.products;
  if (product_id) {
    products = products.filter((p) => p.product_id === product_id);
    if (!products.length) fail("PRODUCT_NOT_FOUND", "That product is not in the catalog.", false, 404);
  }

  const quotes = products
    .map((p) => buildQuote(db, intent!, p))
    // Hard requirements that clearly fail are filtered out; "unknown" stays visible but flagged.
    .filter((q) => product_id || !q.checks.some((c) => c.result === "fail"))
    .sort((a, b) => a.total.amount_minor - b.total.amount_minor)
    .slice(0, 4);

  for (const q of quotes) {
    db.quotes.push(q);
    log(db, q.quote_id, "ai", "QUOTE_CREATED", `Quoted ${q.product.name} v${q.product.version} at ${formatMoney(q.total)}`);
  }
  save(db);
  return { quotes };
}

function diff(approval: ApprovalSnapshot, p: MerchantProduct): FieldChange[] {
  const a = approval.approved_fields;
  const out: FieldChange[] = [];
  if (a.total.currency !== p.price.currency) {
    out.push({ field: "currency", label: "Currency", before: a.total.currency, after: p.price.currency });
  }
  if (a.total.amount_minor !== p.price.amount_minor * a.quantity) {
    out.push({ field: "price", label: "Price", before: formatMoney(a.total), after: formatMoney({ amount_minor: p.price.amount_minor * a.quantity, currency: p.price.currency }) });
  }
  if (a.variant_id !== p.variant_id) {
    out.push({ field: "variant", label: "Item / variant", before: variantLabel(p, a.variant_id), after: variantLabel(p, p.variant_id) });
  }
  if (a.merchant_name !== p.merchant.name) {
    out.push({ field: "merchant", label: "Merchant", before: a.merchant_name, after: p.merchant.name });
  }
  if (a.return_days !== p.return_policy.days) {
    const f = (d: number | null) => (d === null ? "Not stated" : `${d} days`);
    out.push({ field: "return_policy", label: "Return window", before: f(a.return_days), after: f(p.return_policy.days) });
  }
  return out;
}

/** Applies expiry and change detection the way the backend would before returning a quote. */
function refresh(db: Db, q: Quote): { changes: FieldChange[]; active: ApprovalSnapshot | null } {
  const product = db.products.find((p) => p.product_id === q.product.product_id)!;
  const approvals = db.approvals.filter((a) => a.quote_id === q.quote_id);
  const latest = approvals[approvals.length - 1] ?? null;
  let changes: FieldChange[] = [];

  if (q.status === "PAID") return { changes, active: latest };

  if (latest && latest.status === "ACTIVE") {
    changes = diff(latest, product);
    if (changes.length) {
      latest.status = "INVALIDATED";
      q.status = "REAPPROVAL_REQUIRED";
      log(db, q.quote_id, "system", "APPROVAL_INVALIDATED", `Material change: ${changes.map((c) => c.label).join(", ")}`);
    } else if (Date.parse(latest.expires_at) < Date.now()) {
      latest.status = "EXPIRED";
      q.status = "EXPIRED";
      log(db, q.quote_id, "system", "APPROVAL_EXPIRED", "Approval window elapsed; a fresh approval is required");
    }
  } else if (latest && latest.status === "INVALIDATED") {
    changes = diff(latest, product);
  } else if (!latest && q.status === "QUOTED") {
    if (product.version !== q.product.version) {
      q.status = "REAPPROVAL_REQUIRED";
      changes = diffQuote(q, product);
      log(db, q.quote_id, "system", "QUOTE_STALE", "Product changed after quoting; new quote required");
    } else if (Date.parse(q.expires_at) < Date.now()) {
      q.status = "EXPIRED";
      log(db, q.quote_id, "system", "QUOTE_EXPIRED", "Quote validity elapsed");
    }
  } else if (!latest && q.status === "REAPPROVAL_REQUIRED") {
    changes = diffQuote(q, product);
  }
  return { changes, active: latest && latest.status === "ACTIVE" ? latest : null };
}

function diffQuote(q: Quote, p: MerchantProduct): FieldChange[] {
  const pseudo: ApprovalSnapshot = {
    approval_id: "",
    quote_id: q.quote_id,
    approved_fields: {
      merchant_name: q.product.merchant.name,
      product_name: q.product.name,
      variant_id: q.product.variant_id,
      product_version: q.product.version,
      quantity: q.quantity,
      total: q.total,
      return_days: q.product.return_policy.days,
      policy_version: q.product.return_policy.policy_version,
    },
    approved_at: "",
    expires_at: "",
    status: "INVALIDATED",
  };
  return diff(pseudo, p);
}

export async function getQuote(quote_id: string): Promise<QuoteDetail> {
  await delay(250);
  const db = load();
  const q = db.quotes.find((x) => x.quote_id === quote_id);
  if (!q) fail("QUOTE_NOT_FOUND", "Quote not found, or it belongs to another user.", false, 404);
  const { changes, active } = refresh(db, q!);
  const intent = db.intents.find((i) => i.intent_id === q!.intent_id)!;

  const alternatives = db.products
    .filter((p) => p.product_id !== q!.product.product_id)
    .filter((p) => !evaluate(intent, toVersion(p)).some((c) => c.result === "fail"))
    .slice(0, 3)
    .map((p) => ({ product_id: p.product_id, name: p.name, price: p.price, merchant: p.merchant.name }));

  save(db);
  return {
    ...structuredClone(q!),
    active_approval: active,
    changes,
    requires_new_approval: q!.status === "REAPPROVAL_REQUIRED" || q!.status === "EXPIRED",
    alternatives,
    payment_attempt_id: db.quotePayments[quote_id] ?? null,
  };
}

export async function getQuoteEvents(quote_id: string): Promise<AuditEvent[]> {
  await delay(150);
  const db = load();
  return db.events
    .filter((e) => e.quote_id === quote_id)
    // eslint-disable-next-line @typescript-eslint/no-unused-vars
    .map(({ quote_id: _q, ...e }) => e);
}

// ---------------- Approvals ----------------

export async function approve(quote_id: string): Promise<ApprovalSnapshot> {
  await delay(500);
  const db = load();
  const q = db.quotes.find((x) => x.quote_id === quote_id);
  if (!q) fail("QUOTE_NOT_FOUND", "Quote not found.", false, 404);
  refresh(db, q!);
  if (q!.status === "EXPIRED") { save(db); fail("QUOTE_EXPIRED", "This quote has expired. Get a fresh quote to continue.", false); }
  if (q!.status === "REAPPROVAL_REQUIRED") { save(db); fail("QUOTE_CHANGED", "The product changed after this quote was made. Get a fresh quote.", false); }
  if (q!.status !== "QUOTED") { save(db); fail("INVALID_STATE", `Quote is ${q!.status}; it cannot be approved again.`, false); }
  if (q!.checks.some((c) => c.result === "fail")) fail("REQUIREMENTS_NOT_MET", "This item does not meet your stated requirements.", false, 422);

  const p = q!.product;
  const snap: ApprovalSnapshot = {
    approval_id: id(db, "apr"),
    quote_id,
    approved_fields: {
      merchant_name: p.merchant.name,
      product_name: p.name,
      variant_id: p.variant_id,
      product_version: p.version,
      quantity: q!.quantity,
      total: { ...q!.total },
      return_days: p.return_policy.days,
      policy_version: p.return_policy.policy_version,
    },
    approved_at: now(),
    expires_at: new Date(Date.now() + APPROVAL_TTL_MS).toISOString(),
    status: "ACTIVE",
  };
  db.approvals.push(snap);
  q!.status = "USER_APPROVED";
  log(db, quote_id, "buyer", "USER_APPROVED", `Approved ${p.name} for ${formatMoney(q!.total)}; valid 10 min`);
  save(db);
  return snap;
}

// ---------------- Payments (read-only for the frontend) ----------------

export async function getPayment(attempt_id: string): Promise<PaymentAttempt> {
  await delay(200);
  const db = load();
  const pa = db.payments.find((x) => x.attempt_id === attempt_id);
  if (!pa) fail("PAYMENT_NOT_FOUND", "Payment attempt not found.", false, 404);
  return structuredClone(pa!);
}

/**
 * DEV-ONLY stand-in for the PayPal component, used by MockPaymentStub so the
 * receipt and timeline can be built and demoed before real checkout is wired in.
 * Not used when NEXT_PUBLIC_USE_MOCKS is off.
 */
export async function devSimulatePayment(quote_id: string, outcome: "approve" | "cancel"): Promise<string | null> {
  await delay(700);
  const db = load();
  const q = db.quotes.find((x) => x.quote_id === quote_id)!;
  refresh(db, q);
  const approval = db.approvals.filter((a) => a.quote_id === quote_id && a.status === "ACTIVE").pop();
  if (!approval) {
    save(db);
    fail(q.status === "EXPIRED" ? "APPROVAL_EXPIRED" : "APPROVAL_INVALIDATED",
      q.status === "EXPIRED" ? "Your approval expired before payment." : "The purchase changed after you approved it. Payment was stopped.", false);
  }
  if (outcome === "cancel") {
    log(db, quote_id, "buyer", "CHECKOUT_CANCELLED", "Buyer closed PayPal checkout; no payment taken");
    save(db);
    return null;
  }
  const attempt: PaymentAttempt = {
    attempt_id: id(db, "pay"),
    approval_id: approval!.approval_id,
    state: "PAID",
    paypal_order_id: "MOCK-ORDER-" + Math.random().toString(36).slice(2, 10).toUpperCase(),
    capture_id: "MOCK-CAPTURE-" + Math.random().toString(36).slice(2, 10).toUpperCase(),
    amount: { ...approval!.approved_fields.total },
    merchant_name: approval!.approved_fields.merchant_name,
    captured_at: now(),
  };
  log(db, quote_id, "system", "PRE_CREATE_VALIDATED", "Approval still matches catalog; order allowed");
  log(db, quote_id, "paypal", "PAYPAL_ORDER_CREATED", `Order ${attempt.paypal_order_id} (mock)`);
  log(db, quote_id, "buyer", "PAYER_APPROVED", "Buyer approved in PayPal checkout (mock)");
  log(db, quote_id, "system", "PRE_CAPTURE_VALIDATED", "Bound order and amount verified");
  log(db, quote_id, "paypal", "CAPTURED", `Capture ${attempt.capture_id} for ${formatMoney(attempt.amount)} (mock)`);
  approval!.status = "USED";
  q.status = "PAID";
  db.payments.push(attempt);
  db.quotePayments[quote_id] = attempt.attempt_id;
  save(db);
  return attempt.attempt_id;
}

// ---------------- Merchant panel ----------------

export async function listMerchantProducts(): Promise<MerchantProduct[]> {
  await delay(200);
  return structuredClone(load().products);
}

export async function updateMerchantProduct(product_id: string, u: MerchantUpdate): Promise<MerchantProduct> {
  await delay(400);
  const db = load();
  const p = db.products.find((x) => x.product_id === product_id);
  if (!p) fail("PRODUCT_NOT_FOUND", "Product not found.", false, 404);
  const changed: string[] = [];
  if (u.price_minor !== undefined && u.price_minor !== p!.price.amount_minor) {
    if (!Number.isInteger(u.price_minor) || u.price_minor <= 0) fail("INVALID_PRICE", "Price must be a positive amount.", false, 422);
    changed.push(`price ${formatMoney(p!.price)} → ${formatMoney({ amount_minor: u.price_minor, currency: p!.price.currency })}`);
    p!.price.amount_minor = u.price_minor;
  }
  if (u.currency && u.currency !== p!.price.currency) {
    changed.push(`currency ${p!.price.currency} → ${u.currency}`);
    p!.price.currency = u.currency;
  }
  if (u.variant_id && u.variant_id !== p!.variant_id) {
    changed.push(`variant → ${variantLabel(p!, u.variant_id)}`);
    p!.variant_id = u.variant_id;
  }
  if (u.return_days !== undefined && u.return_days !== p!.return_policy.days) {
    changed.push(`returns ${p!.return_policy.days ?? "none"} → ${u.return_days ?? "none"} days`);
    p!.return_policy.days = u.return_days;
    p!.return_policy.policy_version = "rp-" + (parseInt(p!.return_policy.policy_version.split("-")[1] ?? "1", 10) + 1);
  }
  if (changed.length) {
    p!.version += 1;
    for (const q of db.quotes.filter((x) => x.product.product_id === product_id && x.status !== "PAID")) {
      log(db, q.quote_id, "merchant", "PRODUCT_CHANGED", `${p!.name} v${p!.version}: ${changed.join("; ")}`);
    }
  }
  save(db);
  return structuredClone(p!);
}

export async function resetDemo(): Promise<void> {
  await delay(200);
  save(emptyDb());
}
