# IntentLock 🔒

> **Zero-Surprise AI Commerce Engine** with deterministic consent validation, real-time merchant policy enforcement, and PayPal Sandbox checkout integration.

[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg?style=flat&logo=fastapi)](https://fastapi.tiangolo.com)
[![Python](https://img.shields.io/badge/Python-3.13+-3776AB.svg?style=flat&logo=python)](https://python.org)
[![Coverage](https://img.shields.io/badge/coverage-100%25-brightgreen.svg?style=flat)](https://github.com/grimscar7/IntentLock)
[![Tests](https://img.shields.io/badge/tests-211%20passed-success.svg?style=flat)](https://github.com/grimscar7/IntentLock)
[![Code style](https://img.shields.io/badge/code%20style-ruff-000000.svg?style=flat)](https://github.com/astral-sh/ruff)
[![Type Checked](https://img.shields.io/badge/types-mypy-blue.svg?style=flat)](https://mypy-lang.org/)

---

## 💡 The Problem

In conversational and autonomous AI commerce, autonomous agents often browse catalogs, generate quotes, and negotiate transactions on behalf of users. However:
- **Bait-and-Switch**: Merchant catalog items, prices, shipping fees, or return policies can change between quote creation and payment capture.
- **Agent Hallucinations**: Generative AI models can misquote prices, misinterpret user budgets, or accept unauthorized merchant substitutions.
- **Replay & Concurrency Exploits**: Buyers or malicious scripts can replay old, under-priced quotes or initiate duplicate captures concurrently.

## 🛡️ The Solution: IntentLock

IntentLock inserts an immutable, cryptographic-grade **Consent & Policy Validation Barrier** between AI shopping interactions and payment capture:
1. **Deterministic Intent Extraction**: User requirements (budget, specifications, return policies) are structured and bounded by strict schema constraints.
2. **Server-Authoritative Quoting**: Client-submitted prices are discarded; quotes are generated strictly against immutable `ProductVersion` records.
3. **Dual-Gate Pre-Flight Consent**:
   - **Gate 1 (`validate_before_create`)**: Validates active consent and immutable quote consistency before the payment order is created.
   - **Gate 2 (`validate_before_capture`)**: Validates active consent, catalog integrity, price locks, and return policies right before capture.
4. **Instant Invalidation on Material Change**: If a merchant spikes prices or changes terms, approvals are instantly invalidated and plain-English grounded explanations are generated for the buyer.
5. **Replay & Double-Spend Immunity**: Approvals transition atomically from `ACTIVE` to `CONSUMED`. Replaying or re-capturing is blocked at the database engine level.

---

## 🏛️ System Architecture

```mermaid
flowchart TD
    User([User / AI Agent]) -->|Natural Language Intent| API[FastAPI Gateway]
    API --> IntentEngine[Intent & Rule Engine]
    IntentEngine --> DB[(PostgreSQL / SQLite)]
    
    API -->|Generate Quote| QuoteEngine[Authoritative Quote Engine]
    QuoteEngine --> DB
    
    User -->|Grant Explicit Approval| ConsentEngine[Consent Engine]
    ConsentEngine -->|Create Snapshot| Snapshot[(Approval Snapshot)]
    
    subgraph Payment Flow [Protected PayPal Execution]
        Gate1{Gate 1: Pre-Order Validation}
        PayPalOrder[Create PayPal Order]
        BuyerAuth[Buyer Authorizes PayPal]
        Gate2{Gate 2: Pre-Capture Validation}
        Capture[Capture PayPal Payment]
        Consume[Consume Snapshot atomically]
    end
    
    User --> Gate1
    Gate1 -->|Pass| PayPalOrder
    PayPalOrder --> BuyerAuth
    BuyerAuth --> Gate2
    Gate2 -->|Pass| Capture
    Capture --> Consume
    Gate1 -.->|Price or Term Changed| Invalidate[Invalidate & Explain Change]
    Gate2 -.->|Price or Term Changed| Invalidate
```


---

## 🔒 Core Capabilities & Guarantees

### 1. Grounded Intent & Options Engine
- **Structured Criteria Extraction**: Natural language inputs are parsed into structured constraints: `category`, `max_total_minor`, `currency`, `min_ram_gb`, `min_storage_gb`, `brand_preferences`, and `min_return_days`.
- **Code-Based Catalog Filtering**: AI never decides product eligibility; SQL / Python code-level evaluation strictly checks matching items against active `ProductVersion` records.
- **Truthful Options Explanation**: Products that fail constraints receive grounded rejection reasons (e.g. `Price $550 exceeds your maximum budget of $500 USD`).

### 2. Authoritative Dynamic Quoting
- **Zero Client Price Trust**: Client requests only supply `product_id` and `quantity`. Price, shipping, and tax are calculated server-side from active catalog records.
- **Quote Expiration & Revocation**: Quotes feature a strict time-to-live (`15 minutes` default). Expired quotes cannot be approved or converted into payments.
- **Version Pinning**: Quotes are permanently locked to an immutable `ProductVersion` snapshot.

### 3. Dual-Gate Consent & Tamper Verification
IntentLock implements a strict two-phase validation handshake:
| Checkpoint | When Executed | Security Invariant Verified |
| :--- | :--- | :--- |
| **Gate 1 (`validate_before_create`)** | Before calling PayPal Order Create | Approval is `ACTIVE`, user owns the approval, quote is unexpired, and approved terms match current live product terms. |
| **Gate 2 (`validate_before_capture`)** | Right before calling PayPal Capture | Live catalog price has not spiked, product version has not changed, return policy has not degraded, and total payment matches authorized snapshot. |

### 4. Real-Time Invalidation & Plain-English Change Explanations
When merchant catalog modifications occur:
- Any active approval bound to modified products is instantly invalidated (`ApprovalStatus.INVALIDATED`).
- An explainability engine produces user-friendly diff summaries (e.g., `Price increased by $70.00 (from $480.00 to $550.00); Return policy reduced from 30 days to 14 days`).
- Re-quoting requires explicit approval and verifies that updated terms still respect the buyer's original intent budget.

### 5. Audit Trail & Non-Repudiation
- Every quote creation, approval grant, validation pass/fail, and status change is logged as an immutable `AuditEvent`.
- Sensitive credentials, card details, and auth headers are automatically redacted.
- Chronological timeline endpoints allow users and compliance auditors to inspect the transaction history with cursor pagination.


---

## 🚀 Quickstart & Setup Guide

### Prerequisites
- **Python**: 3.13 or higher
- **PostgreSQL**: Supabase instance or local SQLite (default fallback)
- **PayPal Developer Account**: Sandbox client ID & secret

### 1. Clone & Set Up Virtual Environment

```bash
# Clone the repository
git clone https://github.com/grimscar7/IntentLock.git
cd IntentLock

# Create and activate virtual environment
python -m venv .venv
# On Windows (PowerShell):
.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# Install dependencies
pip install -r backend/requirements.txt
```

### 2. Configure Environment Variables

Create your `.env` file based on the example:
```bash
cp backend/.env.example backend/.env
```

| Variable | Description | Default / Example |
| :--- | :--- | :--- |
| `ENV` | Environment (`development`, `test`, `production`) | `development` |
| `DATABASE_URL` | SQLAlchemy connection string | `sqlite:///./intentlock.db` or PostgreSQL |
| `PAYPAL_CLIENT_ID` | PayPal Sandbox Client ID | `sandbox_client_id` |
| `PAYPAL_CLIENT_SECRET` | PayPal Sandbox Secret | `sandbox_client_secret` |
| `PAYPAL_MODE` | PayPal operating mode | `sandbox` |
| `SUPABASE_JWT_SECRET` | Secret for verifying Supabase auth tokens | `your-32-byte-secret-key-here` |
| `DEMO_ADMIN_TOKEN` | Token for simulated merchant updates | `intentlock_demo_secret_2026` |
| `AI_REWORD_ENABLED` | Enable AI-powered diff reword explanations | `false` |

### 3. Database Migration & Catalog Seeding

Apply Alembic migrations to construct the database schema:
```bash
alembic -c backend/alembic.ini upgrade head
```

Seed the default merchant catalog with baseline laptop inventory:
```bash
python -m backend.scripts.seed
```

### 4. Start Development Server

Launch FastAPI with hot-reloading:
```bash
uvicorn backend.api.main:app --host 0.0.0.0 --port 8000 --reload
```

Verify service readiness:
```bash
curl http://localhost:8000/health
# Response: {"status": "ok", "database": "connected"}
```


---

## 📡 API Reference & End-to-End Walkthrough

### Key Endpoints

| Category | Method | Path | Description |
| :--- | :--- | :--- | :--- |
| **Intents** | `POST` | `/api/intents` | Create a new buyer purchase intent from text |
| **Intents** | `GET` | `/api/intents/{id}/options` | Fetch eligible catalog options with explainability |
| **Intents** | `POST` | `/api/intents/{id}/requirements` | Update budget or specs (chains new intent) |
| **Quotes** | `POST` | `/api/quotes` | Generate server-authoritative quote |
| **Quotes** | `GET` | `/api/quotes/{id}` | Retrieve quote details |
| **Quotes** | `POST` | `/api/quotes/{id}/requote` | Re-quote an invalidated or expired quote |
| **Quotes** | `GET` | `/api/quotes/{id}/events` | Paginated chronological audit event timeline |
| **Approvals** | `POST` | `/api/approvals` | Grant user consent and create `ApprovalSnapshot` |
| **Approvals** | `GET` | `/api/approvals/{id}` | Inspect approval snapshot status |
| **Approvals** | `POST` | `/api/approvals/{id}/validate` | Pre-flight dry-run validation check |
| **Catalog Demo** | `PATCH` | `/api/demo/products/{id}` | Simulate merchant price/term updates (Admin token) |
| **Catalog Demo** | `POST` | `/api/demo/reset` | Reset demo products back to pristine seed state |

### Complete Checkout & Tamper Simulation Flow

```bash
# 1. Register buyer intent
curl -X POST http://localhost:8000/api/intents \
  -H "Content-Type: application/json" \
  -H "X-User-Id: user_buyer" \
  -d '{"raw_text": "Need a refurbished laptop under $500 with 16GB RAM and 30-day return policy"}'

# 2. Query matching products with grounded explanations
curl http://localhost:8000/api/intents/<INTENT_ID>/options \
  -H "X-User-Id: user_buyer"

# 3. Create quote for qualified item (e.g., laptop_b)
curl -X POST http://localhost:8000/api/quotes \
  -H "Content-Type: application/json" \
  -H "X-User-Id: user_buyer" \
  -d '{"intent_id": "<INTENT_ID>", "product_id": "laptop_b", "quantity": 1}'

# 4. Lock user consent (produces active ApprovalSnapshot)
curl -X POST http://localhost:8000/api/approvals \
  -H "Content-Type: application/json" \
  -H "X-User-Id: user_buyer" \
  -d '{"quote_id": "<QUOTE_ID>"}'

# 5. Pre-flight check before PayPal Order creation
curl -X POST http://localhost:8000/api/approvals/<APPROVAL_ID>/validate \
  -H "X-User-Id: user_buyer"
# -> {"allowed": true, "reason_code": "OK", "requires_new_approval": false}

# 6. Simulate merchant price spike while buyer is at checkout
curl -X PATCH http://localhost:8000/api/demo/products/laptop_b \
  -H "Content-Type: application/json" \
  -H "X-Demo-Admin-Token: intentlock_demo_secret_2026" \
  -d '{"price_minor": 55000}'

# 7. Pre-flight check again -> Instantly blocked!
curl -X POST http://localhost:8000/api/approvals/<APPROVAL_ID>/validate \
  -H "X-User-Id: user_buyer"
# -> {"allowed": false, "reason_code": "APPROVAL_INVALIDATED", "requires_new_approval": true}
```

---

## 🧪 Testing & Quality Gates

IntentLock maintains a strict **100% line coverage standard** across every backend module.

### Run All Tests with Coverage Report
```bash
python -m pytest backend/api/tests --cov=backend/api --cov-report=term-missing
```

### Static Analysis & Type Checking
```bash
# Code style and linting with Ruff
ruff check --config backend/ruff.toml backend

# Static typing validation with Mypy
mypy --explicit-package-bases backend/api
```

### Test Suite Structure
- **`backend/api/tests/test_phase1_foundation.py`**: Models, enums, DB schema invariants, and catalog seeding.
- **`backend/api/tests/test_phase2_intents_quotes.py`**: Intent extraction, options evaluation, dynamic quoting, and TTL expiration.
- **`backend/api/tests/test_phase4_consent_validators.py`**: Dual-gate consent validation, snapshot immutability, and consumption atomicity.
- **`backend/api/tests/test_phase5_merchant_changes.py`**: Merchant update handling, auto-invalidation, and requote flows.
- **`backend/api/tests/e2e/test_journeys.py`**: End-to-end buyer workflows and edge cases.
- **`backend/api/tests/e2e/test_security.py`**: Prompt injection defense, SQL injection, IDOR auth isolation, and tamper resistance.
- **`backend/api/tests/e2e/test_concurrency.py`**: Race condition checks and double-capture prevention.
- **`backend/api/tests/integration/test_db_integrity.py`**: Foreign key integrity, unique constraints, and transaction rollbacks.
- **`backend/api/tests/test_coverage_gaps.py`**: Comprehensive edge coverage closing all defensive statement branches (100% coverage).


---

## 🛠️ Tech Stack Matrix

| Layer | Technology | Purpose |
| :--- | :--- | :--- |
| **API Framework** | FastAPI (Python 3.13) | High-performance asynchronous REST endpoints & OpenAPI specs |
| **Data Validation** | Pydantic v2 | Strict schema enforcement, input coercion guards, and model serialization |
| **ORM & Database** | SQLAlchemy 2.0 & Alembic | Robust relational data models, transactional isolation, and schema migrations |
| **Database** | PostgreSQL / SQLite | ACID compliance, row locking, and foreign key cascades |
| **Payment Gateway** | PayPal Orders & Capture API | Industry standard checkout execution in PayPal Sandbox |
| **Testing** | Pytest, pytest-cov | 100% statement coverage verification and automated regressions |
| **Linting & Typing** | Ruff, Mypy | Strict formatting, linting rules, and explicit static typing |

---

## 📦 Production Deployment

### Docker Deployment
Build and run the production container:
```bash
# Build the Docker image
docker build -t intentlock-backend -f backend/Dockerfile .

# Run container on port 8000
docker run -d -p 8000:8000 --env-file backend/.env --name intentlock-api intentlock-backend
```

### Google Cloud Run Deployment
Deploy directly using Google Cloud SDK:
```bash
gcloud run deploy intentlock-backend \
  --source . \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated
```

---

## 🤝 Contributing & Code Standards

1. **Fork & Branch**: Create feature branches starting from `backend` (e.g. `feat/new-payment-rail`).
2. **Coverage Invariant**: Every pull request must maintain **100% statement coverage**. Run `pytest --cov=backend/api --cov-report=term-missing`.
3. **Format & Type Check**: All code must pass `ruff check --config backend/ruff.toml backend` and `mypy --explicit-package-bases backend/api`.
4. **Commits**: Follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `test:`, `chore:`).

---

## 📄 License

This project is licensed under the Apache 2.0 License. See the [LICENSE](LICENSE) file for details.
