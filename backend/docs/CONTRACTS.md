# IntentLock Architecture & Data Contracts

> **FROZEN DATA CONTRACTS FOR HARI (PAYPAL INTEGRATION) AND SABARI (FRONTEND)**  
> **Status:** Phase 4 (Consent Validators & Handshake) Locked  
> **Author:** Vishnu (Backend Lead)  
> **Rule:** Field names, types, and enum values in this document are immutable across all branches.

---

## 1. Golden Rules

1. **Money Rule:** All monetary amounts MUST be represented as non-negative integers in minor units named `*_minor` (e.g., USD $480.00 = `48000`). Floating-point numbers are strictly forbidden for pricing calculations and storage.
2. **Timestamps:** All timestamps are ISO 8601 strings in UTC timezone (`YYYY-MM-DDTHH:MM:SSZ`).
3. **Immutability:** `ProductVersion` and `ApprovalSnapshot` rows are append-only / immutable history. When merchant data changes, a new version is created; snapshots are never mutated retroactively.
4. **Ownership Boundary & Call Order for Hari:**
   Hari must follow this exact call order in `paypal-integration`:
   ```
   1. validate_before_create(db, user_id, approval_id)
         │  (Must return allowed=True, otherwise ABORT order creation)
         ▼
   2. Create PayPal Order (bound to payment_attempt with approval_id)
         │
         ▼
   3. Buyer Approves on PayPal Modal
         │
         ▼
   4. validate_before_capture(db, user_id, payment_attempt_id)
         │  (Must return allowed=True, otherwise ABORT capture)
         ▼
   5. Capture PayPal Order
         │
         ▼
   6. consume_approval(db, approval_id)
         (Ensures snapshot can never be re-captured)
   ```

---

## 2. Frozen Field Names

The following field names are standardized across database tables, API payloads, and inter-service interfaces:

| Field Name | Type | Description |
| :--- | :--- | :--- |
| `user_id` | `string` | Unique identifier of the authenticated buyer |
| `intent_id` | `string (UUID)` | Primary key of the buyer's search intent |
| `product_id` | `string` | Base identifier of the catalog item (e.g., `laptop_b`) |
| `product_version_id` | `string` | Primary key of the immutable product snapshot (e.g., `laptop_b_v1`) |
| `quote_id` | `string (UUID)` | Primary key of the binding merchant quote |
| `approval_id` | `string (UUID)` | Primary key of the frozen consent approval snapshot |
| `payment_attempt_id` | `string (UUID)` | Primary key of the payment execution attempt |
| `currency` | `string (ISO 4217)` | 3-letter currency code (e.g., `USD`) |
| `total_minor` | `integer` | Total amount in minor units (`>= 0`) |
| `expires_at` | `timestamp (UTC)` | Absolute expiration time for quotes and approvals |
| `status` | `string (Enum)` | Lifecycle status (see enums below) |

---

## 3. Canonical Enums & Reason Codes

### 3.1 `ReasonCode`
Canonical reason codes returned in `ValidationResult`:

| ReasonCode | Meaning | requires_new_approval |
| :--- | :--- | :---: |
| `OK` | Terms strictly match snapshot and catalog | `false` |
| `APPROVAL_NOT_FOUND` | Approval missing or owned by another user | `false` |
| `APPROVAL_EXPIRED` | Approval snapshot TTL has elapsed | `true` |
| `APPROVAL_NOT_ACTIVE` | Approval is not in ACTIVE status | `true` |
| `APPROVAL_SUPERSEDED` | Buyer approved a newer quote for this product | `true` |
| `APPROVAL_INVALIDATED` | Snapshot invalidated due to catalog alteration | `true` |
| `APPROVAL_ALREADY_CONSUMED` | Approval already captured by another payment | `true` |
| `QUOTE_INVALID` | Bound quote not found or not in APPROVED status | `true` |
| `ITEM_MISMATCH` | Product ID differs from approved snapshot | `true` |
| `MERCHANT_MISMATCH` | Merchant ID differs from approved snapshot | `true` |
| `CURRENCY_MISMATCH` | Currency differs between approval and order | `true` |
| `QUANTITY_MISMATCH` | Quantity differs between quote and approval | `true` |
| `AMOUNT_MISMATCH` | Total amount differs between approval and attempt | `true` |
| `QUOTE_CHANGED` | Unit price, total, or RAM specs modified | `true` |
| `PRODUCT_VERSION_CHANGED` | Product unavailable or catalog specs altered | `true` |
| `POLICY_CHANGED` | Return days or policy version altered | `true` |
| `REQUIREMENTS_NO_LONGER_MET` | Current product fails stored intent rules | `true` |
| `PAYMENT_ATTEMPT_NOT_FOUND` | PaymentAttempt missing or owned by another user | `false` |
| `PAYMENT_ATTEMPT_UNBOUND` | PaymentAttempt lacks an approval_id binding | `true` |
| `VALIDATION_ERROR` | Unexpected internal failure (fails closed) | `false` |

### 3.2 Reason Priority Order
When multiple discrepancies occur simultaneously, all differences are recorded in `changes[]`, and the primary `reason_code` is resolved in this exact priority:
1. `ITEM_MISMATCH`
2. `MERCHANT_MISMATCH`
3. `CURRENCY_MISMATCH`
4. `QUANTITY_MISMATCH`
5. `AMOUNT_MISMATCH` / `QUOTE_CHANGED`
6. `POLICY_CHANGED`
7. `PRODUCT_VERSION_CHANGED`
8. `REQUIREMENTS_NO_LONGER_MET`

---

## 4. Consent Validator Signatures & Usage (For Hari)

These python functions live in `backend/api/consent/service.py` and are imported directly into Hari's payment service:

### 4.1 `validate_before_create`
```python
def validate_before_create(
    db: Session,
    user_id: str,
    approval_id: str,
    *,
    record_audit: bool = True
) -> ValidationResult
```
- **When to call:** Directly before creating a PayPal order.
- **Fail Closed:** Returns `allowed: false` on any validation failure or unexpected error. Never raises.
- **Audit Emission:** Automatically records `PRE_CREATE_VALIDATION_PASSED` or `PRE_CREATE_VALIDATION_FAILED`.

### 4.2 `validate_before_capture`
```python
def validate_before_capture(
    db: Session,
    user_id: str,
    payment_attempt_id: str,
    *,
    record_audit: bool = True
) -> ValidationResult
```
- **When to call:** Directly before capturing funds from PayPal.
- **Security Guarantee:** Resolves `approval_id` strictly from the bound `PaymentAttempt` row. Client-supplied approval IDs are never accepted.
- **Audit Emission:** Automatically records `PRE_CAPTURE_VALIDATION_PASSED` or `PRE_CAPTURE_VALIDATION_FAILED`.

### 4.3 `consume_approval`
```python
def consume_approval(db: Session, approval_id: str) -> bool
```
- **When to call:** Directly after PayPal capture successfully completes.
- **Atomic Guarantee:** Transitions `ACTIVE` -> `CONSUMED`. Returns `True` exactly once. Subsequent calls return `False` (blocking double captures).

---

## 5. Validation Result Schemas & JSON Payloads

### Sample 1: Successful Validation (`allowed = true`)
```json
{
  "allowed": true,
  "reason_code": "OK",
  "changes": [],
  "requires_new_approval": false,
  "message": "Consent verified: all terms match approved snapshot.",
  "approval_id": "03257cdd-f05d-4dba-9a60-b3146b4a94e8",
  "quote_id": "5a52cc20-588c-41b5-948d-eef3086febbf",
  "checked_at": "2026-11-11T05:32:32.974654Z"
}
```

### Sample 2: Blocked Validation - Merchant Price Increase (`allowed = false`)
```json
{
  "allowed": false,
  "reason_code": "QUOTE_CHANGED",
  "changes": [
    {
      "field": "price_minor",
      "approved": 48000,
      "current": 54000
    },
    {
      "field": "total_minor",
      "approved": 48000,
      "current": 54000
    },
    {
      "field": "product_version_id",
      "approved": "laptop_b_v1",
      "current": "laptop_b_v2"
    }
  ],
  "requires_new_approval": true,
  "message": "The approved price was $480.00. The current price is $540.00. Fresh approval is required.",
  "approval_id": "03257cdd-f05d-4dba-9a60-b3146b4a94e8",
  "quote_id": "5a52cc20-588c-41b5-948d-eef3086febbf",
  "checked_at": "2026-11-11T05:35:10.123456Z"
}
```

---

## 6. Preflight Route for Frontend (For Sabari)

### `POST /api/approvals/{approval_id}/validate`
Read-only dry run check for UI before presenting the PayPal button:
- **Header:** `Authorization: Bearer <jwt>` (or `X-User-Id: <user_id>` in dev)
- **Effect:** Runs `validate_before_create(..., record_audit=False)`. Does not emit audit logs or mutate state.
- **Response:** Returns `ValidationResult` (see examples above).

---

## 7. Demo Endpoints (Admin Controlled)

Guarded by `X-Demo-Admin-Token` equal to `DEMO_ADMIN_TOKEN` and non-production environment (`dev`, `staging`, `demo`). Returns 404 in production, 403 on invalid token.

### `PATCH /api/demo/products/{product_id}`
Simulates merchant price spikes or policy updates during live demos:
- **Header:** `X-Demo-Admin-Token: <token>`
- **Body:**
  ```json
  {
    "price_minor": 55000,
    "return_days": 14,
    "available": true
  }
  ```
- **Side Effects:**
  - Creates next immutable `ProductVersion` (`v(N+1)`).
  - Material changes (price increase, return days decrease, availability false, policy version bump) atomically invalidate all active quotes and active approvals pointing to the old product version.
  - Consumed approvals remain untouched.
  - Emits `PRODUCT_VERSION_CHANGED` and `APPROVAL_INVALIDATED` audit events.

### `POST /api/demo/reset`
Restores catalog to original 5 seeded laptops with v1 versions for repeatable demo runs.

---

## 8. Reapproval & Intent Refinement Flow

### `POST /api/quotes/{quote_id}/requote`
Re-evaluates catalog changes for an existing quote:
- **Header:** `Authorization: Bearer <jwt>` (or `X-User-Id: <user_id>` in dev)
- **If new version exceeds original intent criteria / budget:**
  - Status: `400 Bad Request`
  - Body:
    ```json
    {
      "error": {
        "code": "PRODUCT_NOT_ELIGIBLE",
        "message": "Product exceeds intent criteria",
        "details": {
          "requires_requirement_change": true,
          "reasons": ["Calculated quote total (55000) exceeds intent budget (50000)"],
          "changes": [{"field": "price_minor", "approved": 48000, "current": 55000}]
        }
      }
    }
    ```
- **If still eligible within intent criteria:**
  - Status: `201 Created`
  - Returns `RequoteResponse` containing `new_quote` and `comparison` (changes, deterministic base message, and validated AI-worded explanation).

### `POST /api/intents/{intent_id}/requirements`
Allows buyer to increase their budget or adjust constraints when a price increase exceeds original limits:
- **Header:** `Authorization: Bearer <jwt>` (or `X-User-Id: <user_id>` in dev)
- **Body:**
  ```json
  {
    "max_total_minor": 60000
  }
  ```
- **Response:** Creates and returns a new chained `Intent` (`previous_intent_id` pointing to the prior intent) with updated constraints.

---

## 9. Lifecycle Audit Timeline

### `GET /api/quotes/{quote_id}/events`
Returns complete, chronological, ownership-enforced audit timeline for a quote, its approvals, and its payment attempts:
- **Header:** `Authorization: Bearer <jwt>` (or `X-User-Id: <user_id>` in dev)
- **Query Params:** `limit=100`, `cursor=<event_id>`
- **Response:**
  ```json
  {
    "quote_id": "5a52cc20-588c-41b5-948d-eef3086febbf",
    "total_events": 4,
    "events": [
      {
        "event_id": "018f...",
        "event_type": "QUOTE_CREATED",
        "created_at": "2026-11-11T05:30:00Z",
        "title": "Quote Created",
        "description": "Quote generated for 1 item(s) at total $480.00.",
        "payload": { "total_minor": 48000, "currency": "USD" }
      },
      {
        "event_id": "018f...",
        "event_type": "APPROVAL_GRANTED",
        "created_at": "2026-11-11T05:32:00Z",
        "title": "Buyer Approved",
        "description": "Buyer approved terms for product 'laptop_b' on quote ...",
        "payload": { ... }
      },
      {
        "event_id": "018f...",
        "event_type": "APPROVAL_INVALIDATED",
        "created_at": "2026-11-11T05:35:00Z",
        "title": "Approval Invalidated",
        "description": "Approval ... was invalidated due to price increased from 48000 to 55000.",
        "payload": { ... }
      }
    ],
    "next_cursor": null
  }
  ```
- Sensitive fields (tokens, secrets, credentials, keys) are recursively redacted to `[REDACTED]`.
