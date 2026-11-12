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
