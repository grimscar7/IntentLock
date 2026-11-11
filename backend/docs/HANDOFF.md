# IntentLock Backend Handoff & Integration Guide

**Owner:** Vishnu (Backend Lead)  
**Branch:** `backend`  
**Target:** `main`  
**Status:** Phases 1–6 Complete (Verified, 111 Tests Passing, 93% Coverage)

---

## 1. What Is Built

All backend routes for intent extraction, product filtering, binding quotes, immutable consent snapshots, deterministic validation, audit timelines, and demo controls are implemented and live.

| Method | Path | Purpose | Authentication |
| :--- | :--- | :--- | :--- |
| `GET` | `/health` | Liveness & database connection health check | Public |
| `POST` | `/api/intents` | Register natural language shopping intent & extract structured requirements | Bearer JWT (dev: `X-User-Id`) |
| `GET` | `/api/intents/{intent_id}` | Retrieve search intent with ownership check | Bearer JWT (dev: `X-User-Id`) |
| `POST` | `/api/intents/{intent_id}/requirements` | Update requirements via intent chaining (`previous_intent_id`) | Bearer JWT (dev: `X-User-Id`) |
| `GET` | `/api/catalog/products` | List all current active catalog product versions | Public / Optional Auth |
| `GET` | `/api/intents/{intent_id}/options` | Evaluate catalog eligibility and return grounded AI explanations | Bearer JWT (dev: `X-User-Id`) |
| `POST` | `/api/quotes` | Generate binding expiring merchant quote for a product | Bearer JWT (dev: `X-User-Id`) |
| `GET` | `/api/quotes/{quote_id}` | Retrieve quote details and expiration status | Bearer JWT (dev: `X-User-Id`) |
| `POST` | `/api/quotes/{quote_id}/requote` | Re-quote product following catalog updates, checking budget fit | Bearer JWT (dev: `X-User-Id`) |
| `GET` | `/api/quotes/{quote_id}/events` | Fetch chronological audit trail timeline for a quote | Bearer JWT (dev: `X-User-Id`) |
| `POST` | `/api/approvals` | Freeze quote terms into an immutable `ApprovalSnapshot` | Bearer JWT (dev: `X-User-Id`) |
| `POST` | `/api/approvals/{approval_id}/validate` | Pre-flight dry-run validation for frontend UI before checkout | Bearer JWT (dev: `X-User-Id`) |
| `PATCH` | `/api/demo/products/{product_id}` | Demo admin: atomically bump version (price, specs, policy, stock) | `X-Demo-Admin-Token` (Non-prod) |
| `POST` | `/api/demo/reset` | Demo admin: reset all demo catalog items to baseline seed v1 | `X-Demo-Admin-Token` (Non-prod) |

---

## 2. How to Run Locally

### Prerequisites
- Python 3.11+ (Python 3.13 verified)
- Git

### Quickstart Steps
```bash
# 1. Clone & switch to backend branch
git checkout backend

# 2. Create virtual environment
python -m venv venv
# Windows:
.\venv\Scripts\activate
# Linux/macOS:
source venv/bin/activate

# 3. Install dependencies
pip install -r backend/requirements.txt

# 4. Configure local environment
copy backend\.env.example backend\.env
# Ensure DATABASE_URL is set (default: sqlite:///./intentlock.db for local dev)

# 5. Run database migrations to head
alembic -c backend/alembic.ini upgrade head

# 6. Seed catalog with 5 reference laptops
python backend/scripts/seed.py

# 7. Start the API server
uvicorn backend.api.main:app --reload --port 8000
```

### Health Check Verification
```bash
curl -X GET http://127.0.0.1:8000/health
# Response: {"status":"ok","database":"connected","timestamp":"..."}
```

---

## 3. Environment Variables Reference

| Variable Name | Default Value | Required in Prod? | Description & Example |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | `sqlite:///./intentlock.db` | **YES** | SQLAlchemy database connection string.<br>Prod: `postgresql+psycopg://postgres:[PWD]@db.[REF].supabase.co:5432/postgres` |
| `TEST_DATABASE_URL` | `sqlite:///:memory:` | No | Database URL dedicated to running automated tests. Guarantees test isolation. |
| `ENV` | `development` | **YES** | Environment flag (`development`, `staging`, `demo`, `prod`). When `prod`, demo admin endpoints return 404 and `X-User-Id` auth fallback is strictly disabled. |
| `SUPABASE_JWT_SECRET` | None | **YES** | Secret key for verifying Supabase HS256 buyer authentication tokens. |
| `AI_PROVIDER` | `gemini` | No | AI engine for intent parsing: `gemini`, `openai`, or `mock`. Fallbacks safely to regex parser when keys are missing. |
| `AI_API_KEY` | None | No | API key for Gemini / OpenAI. |
| `AI_MODEL` | `gemini-2.0-flash` | No | Target foundation model name. |
| `DEMO_ADMIN_TOKEN` | `demo-admin-secret-token` | In demo/staging | Secret bearer token required in `X-Demo-Admin-Token` header for merchant demo changes and catalog reset. |
| `DEMO_ALLOWED_ENVS` | `["dev","development","staging","demo"]` | No | List of environment strings where demo mutation routes are enabled. |
| `QUOTE_TTL_MINUTES` | `15` | No | Expiration window in minutes for generated quotes. |
| `APPROVAL_TTL_MINUTES` | `15` | No | Expiration window in minutes for buyer approval snapshots. |
| `CORS_ORIGINS` | `["http://localhost:3000","http://localhost:5173"]` | In prod | JSON-serialized array of allowed frontend origins for CORS middleware. |

---

## 4. Test & Verification Commands

All quality gates can be executed via terminal commands or the provided `backend/Makefile`:

| Target | Command | Purpose |
| :--- | :--- | :--- |
| **All Tests** | `pytest backend/api/tests` | Run entire suite (111 tests) |
| **Coverage** | `pytest backend/api/tests --cov=backend/api --cov-report=term-missing` | Validate line coverage ($\ge 93\%$) |
| **Lint** | `ruff check backend` | Code formatting & style compliance |
| **Type Check** | `mypy backend/api --explicit-package-bases --ignore-missing-imports` | Static type checks |
| **Security** | `bandit -r backend/api -q` | AST security vulnerability scan |
| **Dependencies** | `pip-audit -r backend/requirements.txt` | CVE audit against Python packages |
| **Live Smoke** | `python backend/scripts/smoke_test.py` | Live HTTP verification against uvicorn |

---

## 5. Deployment Notes for Abdu (DevOps / Deployment Lead)

### Render Web Service Configuration
- **Environment:** Python 3
- **Root Directory:** Repository root (`.`)
- **Build Command:**
  ```bash
  pip install -r backend/requirements.txt
  ```
- **Pre-Deploy Command:**
  ```bash
  alembic -c backend/alembic.ini upgrade head
  ```
- **Start Command:**
  ```bash
  uvicorn backend.api.main:app --host 0.0.0.0 --port $PORT
  ```
- **Health Check Path:**
  ```text
  /health
  ```

### Database Connection String Note (Supabase Postgres)
- Use the **Transaction Pooler** connection string (port 6543) or **Direct Connection** (port 5432) with driver `postgresql+psycopg://`.
- Always append SSL mode query parameters for Supabase:
  ```text
  postgresql+psycopg://postgres:[YOUR-PASSWORD]@db.[YOUR-PROJECT-REF].supabase.co:5432/postgres?sslmode=require
  ```
- Ensure `alembic -c backend/alembic.ini upgrade head` executes before the app container starts.

---

## 6. API Examples (Curl)

### 1. Register Intent
```bash
curl -X POST http://127.0.0.1:8000/api/intents \
  -H "Content-Type: application/json" \
  -H "X-User-Id: buyer_123" \
  -d '{"raw_text": "Need a laptop with at least 16GB RAM under $500 with 30 day returns"}'
# HTTP 201 Created -> returns {"intent_id": "...", "status": "VALIDATED", "requirements": {...}}
```

### 2. Get Grounded Options
```bash
curl -X GET http://127.0.0.1:8000/api/intents/c88638a3-ee80-4626-9733-c60d844f7203/options \
  -H "X-User-Id: buyer_123"
# HTTP 200 OK -> returns {"eligible_products": [{"product_id": "laptop_b", "explanation": "...", ...}]}
```

### 3. Create Quote
```bash
curl -X POST http://127.0.0.1:8000/api/quotes \
  -H "Content-Type: application/json" \
  -H "X-User-Id: buyer_123" \
  -d '{"intent_id": "c88638a3-ee80-4626-9733-c60d844f7203", "product_id": "laptop_b", "quantity": 1}'
# HTTP 201 Created -> returns {"quote_id": "...", "total_minor": 48000, "status": "ACTIVE", ...}
```

### 4. Create Approval Snapshot (Buyer Consent)
```bash
curl -X POST http://127.0.0.1:8000/api/approvals \
  -H "Content-Type: application/json" \
  -H "X-User-Id: buyer_123" \
  -d '{"quote_id": "47ffefa4-a108-47d7-bba9-0bc966f65f4f"}'
# HTTP 201 Created -> returns {"approval_id": "...", "status": "ACTIVE", ...}
```

### 5. Pre-flight UI Validation
```bash
curl -X POST http://127.0.0.1:8000/api/approvals/13d8d143-df57-4549-8a47-857657758015/validate \
  -H "X-User-Id: buyer_123"
# HTTP 200 OK -> returns {"allowed": true, "reason_code": "OK", "changes": [], "requires_new_approval": false}
```

### 6. Merchant Demo Price Spike (Admin)
```bash
curl -X PATCH http://127.0.0.1:8000/api/demo/products/laptop_b \
  -H "Content-Type: application/json" \
  -H "X-Demo-Admin-Token: demo-admin-secret-token" \
  -d '{"price_minor": 55000}'
# HTTP 200 OK -> returns {"product_id": "laptop_b", "version": 2, "is_material": true, "invalidated_approvals_count": 1}
```

### 7. View Event Timeline
```bash
curl -X GET http://127.0.0.1:8000/api/quotes/47ffefa4-a108-47d7-bba9-0bc966f65f4f/events \
  -H "X-User-Id: buyer_123"
# HTTP 200 OK -> returns {"events": [{"event_type": "QUOTE_CREATED"}, {"event_type": "APPROVAL_GRANTED"}, ...]}
```

---

## 7. Integration Guide for Hari (Payments Engineer)

Hari owns the PayPal payment lifecycle in `backend/api/payments/` on branch `paypal-integration`.

### Call Order Flow
```
1. validate_before_create(db, user_id, approval_id)
   │  └── allowed == True? If False, return validation error to buyer. Do NOT call PayPal.
   ▼
2. paypal_service.create_order(...)
   │  └── Record PaymentAttempt row bound to approval_id.
   ▼
3. Buyer Authorizes / Approves in PayPal UI
   │
   ▼
4. validate_before_capture(db, user_id, payment_attempt_id)
   │  └── allowed == True? If False, cancel attempt. Do NOT capture funds.
   ▼
5. paypal_service.capture_order(...)
   │
   ▼
6. consume_approval(db, approval_id)
   └── Atomically marks snapshot CONSUMED. Guarantees single-use.
```

### Code Snippets

#### A. Before PayPal Order Creation (`validate_before_create`)
```python
from backend.api.consent.service import validate_before_create
from backend.api.errors import AppError

validation = validate_before_create(
    db=db,
    user_id=user_id,
    approval_id=approval_id,
    record_audit=True,
)

if not validation.allowed:
    # Stop immediately. Surface explanation to client without creating PayPal order.
    raise AppError(
        code=validation.reason_code.value,
        message=validation.message,
        status_code=409,
        details={
            "requires_new_approval": validation.requires_new_approval,
            "changes": [c.model_dump() for c in validation.changes],
        },
    )

# Proceed with PayPal order creation
order = paypal_client.create_order(
    amount_minor=approval.total_minor,
    currency=approval.currency,
)
```

#### B. Before PayPal Order Capture (`validate_before_capture`)
```python
from backend.api.consent.service import validate_before_capture

# Never trust client approval ID on capture. Validate against bound payment attempt.
validation = validate_before_capture(
    db=db,
    user_id=user_id,
    payment_attempt_id=payment_attempt_id,
    record_audit=True,
)

if not validation.allowed:
    # Abort capture. Funds are not withdrawn.
    raise AppError(
        code=validation.reason_code.value,
        message=validation.message,
        status_code=409,
        details={
            "requires_new_approval": validation.requires_new_approval,
            "changes": [c.model_dump() for c in validation.changes],
        },
    )

# Capture approved PayPal order
capture_res = paypal_client.capture_order(order_id)
```

#### C. After Successful Capture (`consume_approval`)
```python
from backend.api.consent.service import consume_approval

# Once PayPal confirms capture, consume the approval snapshot atomically
success = consume_approval(db=db, approval_id=approval_id)
if not success:
    # Already consumed by a concurrent attempt
    logger.critical("Double capture attempt detected on approval %s", approval_id)
```

### Reason Codes Classification Table

| Reason Code | Category | Is Recoverable? | Recommended Action |
| :--- | :--- | :--- | :--- |
| `OK` | Success | N/A | Proceed to create order / capture |
| `PRICE_CHANGED` | Merchant Change | **Yes** (Recoverable) | Display delta to buyer; prompt to requote & reapprove |
| `SPEC_CHANGED` | Merchant Change | **Yes** (Recoverable) | Display spec delta (RAM, etc.); prompt to reapprove |
| `POLICY_CHANGED` | Merchant Change | **Yes** (Recoverable) | Display policy delta (returns); prompt to reapprove |
| `APPROVAL_INVALIDATED` | Merchant Change | **Yes** (Recoverable) | Trigger requote flow; buyer reapproves new terms |
| `REQUIREMENTS_NO_LONGER_MET` | Invalidation | **Yes** (Recoverable) | Prompt buyer to adjust budget or specs |
| `APPROVAL_EXPIRED` | Timeout | **Yes** (Recoverable) | Quote/approval expired; issue fresh quote |
| `APPROVAL_ALREADY_CONSUMED` | Security/Integrity | **No** (Terminal) | Hard error; snapshot was already paid |
| `APPROVAL_NOT_FOUND` | Security/Auth | **No** (Terminal) | Hard error (404/403) |
| `MERCHANT_MISMATCH` | Integrity | **No** (Terminal) | Hard error; merchant ID modified |
| `CURRENCY_MISMATCH` | Integrity | **No** (Terminal) | Hard error; payment currency modified |
| `AMOUNT_MISMATCH` | Integrity | **No** (Terminal) | Hard error; payment total differs from approval |
| `ITEM_MISMATCH` | Integrity | **No** (Terminal) | Hard error; product version divergence |
| `INSUFFICIENT_STOCK` | Inventory | **No** (Terminal) | Item out of stock; abort |

---

## 8. Integration Guide for Sabari (Frontend Engineer)

### Standard User Checkout Flow
```
User Search Text
       │
       ▼
1. POST /api/intents
       │  └── Returns {intent_id, requirements}
       ▼
2. GET /api/intents/{intent_id}/options
       │  └── Returns {eligible_products: [{title, price_minor, explanation, ...}]}
       ▼
3. POST /api/quotes
       │  └── Returns {quote_id, total_minor, expires_at}
       ▼
4. POST /api/approvals
       │  └── Returns {approval_id, approved_terms, status: "ACTIVE"}
       ▼
5. POST /api/approvals/{approval_id}/validate (Optional Preflight Check)
       │  └── If allowed=True, open PayPal SDK button
       ▼
6. PayPal Checkout Modal Initiated (Hari's Endpoint)
```

### Demo Story: Handling Blocked Validation & Reapproval
When a merchant mutates pricing or terms during checkout:
1. `validate_approval_preflight` returns:
   ```json
   {
     "allowed": false,
     "reason_code": "PRICE_CHANGED",
     "requires_new_approval": true,
     "message": "Merchant price increased from $480.00 to $550.00.",
     "changes": [
       {
         "field": "price_minor",
         "approved": 48000,
         "current": 55000
       }
     ]
   }
   ```
2. Frontend renders a clear comparison modal highlighting the exact `changes` array with approved vs current values.
3. If new price exceeds original budget, call `POST /api/intents/{intent_id}/requirements` to raise budget (`budget_minor: 55000`).
4. Call `POST /api/quotes/{quote_id}/requote` to receive updated quote.
5. Call `POST /api/approvals` to freeze new terms.
6. Trigger checkout on the new `approval_id`.

### Standardized Error Format
All errors return the machine-readable schema:
```json
{
  "error": {
    "code": "STRING_ERROR_CODE",
    "message": "Human readable description",
    "details": {}
  }
}
```

---

## 9. Branch & Merge Plan

- **Source Branch:** `backend`
- **Target Branch:** `main`
- **Protected Files:**
  - `backend/api/payments/*`: Owned exclusively by Hari (`paypal-integration`). Vishnu made zero edits or additions in this folder.
  - `frontend/*`: Owned exclusively by Sabari. Vishnu touched zero files in `frontend/`.
- **Merge Conflict Status:**
  - Merging `origin/main` into `backend` was dry-run tested (`git merge --no-commit --no-ff origin/main`). Zero conflicts were encountered (`Automatic merge went well; stopped before committing as requested`).
- **Readiness:**
  - Branch `backend` is ready for review and merge into `main`.
