# IntentLock Backend

IntentLock backend service built with FastAPI, SQLAlchemy 2.0, Alembic, and Pydantic v2.

## Quickstart

### 1. Environment Setup
```bash
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r backend/requirements.txt
```

### 2. Configuration
Copy the example environment configuration:
```bash
cp backend/.env.example backend/.env
```
Modify `DATABASE_URL` in `.env` to point to your Supabase PostgreSQL instance (or omit to use local SQLite default for offline development).

### 3. Database Migrations
Run Alembic migrations to bring the database schema up to the latest revision:
```bash
alembic -c backend/alembic.ini upgrade head
```

To rollback:
```bash
alembic -c backend/alembic.ini downgrade -1
```

### 4. Seed Catalog Data
Seed the initial catalog with 5 laptop ProductVersions (idempotent):
```bash
python -m backend.scripts.seed
```

### 5. Run API Server
Start the development server with hot-reload:
```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify service and database health:
```bash
curl http://localhost:8000/health
```

### 6. Run Test Suite
Run all unit and integration tests (Phases 1, 2, and 4):
```bash
pytest backend/api/tests -v
```

---

## End-to-End Demo & Pre-Flight Consent Validation Flow

```bash
# 1. Submit buyer purchase intent
curl -X POST http://localhost:8000/api/intents \
  -H "Content-Type: application/json" \
  -H "X-User-Id: demo_user" \
  -d '{"raw_text": "Looking for a refurbished laptop under $500 with 16GB RAM and 30-day returns"}'

# Response gives: "intent_id": "<INTENT_ID>"

# 2. Retrieve code-filtered eligible options with grounded explanations
curl http://localhost:8000/api/intents/<INTENT_ID>/options \
  -H "X-User-Id: demo_user"

# Exactly 1 product qualifies: laptop_b

# 3. Create binding quote (client price is ignored)
curl -X POST http://localhost:8000/api/quotes \
  -H "Content-Type: application/json" \
  -H "X-User-Id: demo_user" \
  -d '{"intent_id": "<INTENT_ID>", "product_id": "laptop_b", "quantity": 1}'

# Response gives: "quote_id": "<QUOTE_ID>", total_minor: 48000

# 4. Explicitly consent and approve terms
curl -X POST http://localhost:8000/api/approvals \
  -H "Content-Type: application/json" \
  -H "X-User-Id: demo_user" \
  -d '{"quote_id": "<QUOTE_ID>"}'

# Response creates ApprovalSnapshot (status: ACTIVE) with "approval_id": "<APPROVAL_ID>"

# 5. Pre-flight Validation (Dry run for Frontend UI / Sabari)
curl -X POST http://localhost:8000/api/approvals/<APPROVAL_ID>/validate \
  -H "X-User-Id: demo_user"

# Returns: {"allowed": true, "reason_code": "OK", "changes": [], "requires_new_approval": false, ...}

# 6. Merchant Price Spike Demo (Admin changes price during checkout)
curl -X PATCH http://localhost:8000/api/demo/products/laptop_b \
  -H "Content-Type: application/json" \
  -H "X-Demo-Admin-Token: intentlock_demo_secret_2026" \
  -d '{"price_minor": 55000}'

# Material change immediately invalidates the active approval and quote!

# 7. Check Pre-flight Validation again
curl -X POST http://localhost:8000/api/approvals/<APPROVAL_ID>/validate \
  -H "X-User-Id: demo_user"

# Blocked! Returns: {"allowed": false, "reason_code": "APPROVAL_INVALIDATED", "requires_new_approval": true}

# 8. Attempt Requote (Exceeds original $500 budget -> requires requirement update)
curl -X POST http://localhost:8000/api/quotes/<QUOTE_ID>/requote \
  -H "X-User-Id: demo_user"

# Returns 400 PRODUCT_NOT_ELIGIBLE: {"requires_requirement_change": true, "reasons": ["...exceeds intent budget..."]}

# 9. Buyer updates budget requirement
curl -X POST http://localhost:8000/api/intents/<INTENT_ID>/requirements \
  -H "Content-Type: application/json" \
  -H "X-User-Id: demo_user" \
  -d '{"max_total_minor": 60000}'

# Returns new chained intent: "intent_id": "<NEW_INTENT_ID>"

# 10. View chronological audit timeline for the transaction
curl http://localhost:8000/api/quotes/<QUOTE_ID>/events \
  -H "X-User-Id: demo_user"

# 11. Reset demo catalog back to pristine initial state
curl -X POST http://localhost:8000/api/demo/reset \
  -H "X-Demo-Admin-Token: intentlock_demo_secret_2026"
```

---

## Consent Handshake Order for Hari (`paypal-integration`)

1. Call `validate_before_create(db, user_id, approval_id)` -> must be `allowed: true`.
2. Create PayPal order and store `PaymentAttempt(payment_attempt_id, user_id, approval_id, total_minor, currency, ...)`.
3. Buyer authorizes transaction in PayPal UI.
4. Call `validate_before_capture(db, user_id, payment_attempt_id)` -> must be `allowed: true`.
5. Capture PayPal payment.
6. Call `consume_approval(db, approval_id)` -> marks snapshot `CONSUMED`, preventing replay.
