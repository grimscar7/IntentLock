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
