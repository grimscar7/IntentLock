"""Comprehensive Phase 1 Foundation test suite."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.api.models import (
    ApprovalSnapshot,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    Intent,
    IntentStatus,
    PaymentAttempt,
    PaymentAttemptStatus,
    ProductVersion,
    Quote,
    QuoteStatus,
)
from backend.scripts.seed import seed_products


def test_health_check_endpoint(client):
    """GET /health must return 200 with database and app status ok."""
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["database"] == "ok"
    assert "environment" in data


def test_model_creation_all_entities(db_session):
    """Verify clean instantiation and persistence of all 6 core models."""
    now = datetime.now(UTC)

    # 1. Intent
    intent = Intent(
        user_id="user_test_1",
        raw_text="Looking for a refurbished laptop under $500 with 16GB RAM and 30-day returns",
        requirements={
            "category": "laptop",
            "max_total_minor": 50000,
            "currency": "USD",
            "minimum_ram_gb": 16,
            "minimum_return_days": 30,
        },
        status=IntentStatus.VALIDATED,
    )
    db_session.add(intent)
    db_session.commit()
    assert intent.intent_id is not None
    assert intent.created_at is not None

    # 2. ProductVersion
    pv = ProductVersion(
        product_id="laptop_test",
        version=1,
        merchant_id="merchant_test",
        title="Test Laptop Pro",
        price_minor=48000,
        currency="USD",
        ram_gb=16,
        return_days=30,
        policy_version=1,
        available=True,
        is_current=True,
    )
    db_session.add(pv)
    db_session.commit()
    assert pv.product_version_id is not None

    # 3. Quote
    quote = Quote(
        user_id="user_test_1",
        intent_id=intent.intent_id,
        product_version_id=pv.product_version_id,
        merchant_id="merchant_test",
        quantity=1,
        unit_price_minor=48000,
        shipping_minor=0,
        tax_minor=0,
        total_minor=48000,
        currency="USD",
        status=QuoteStatus.ACTIVE,
        expires_at=now + timedelta(minutes=15),
    )
    db_session.add(quote)
    db_session.commit()
    assert quote.quote_id is not None

    # 4. ApprovalSnapshot
    approval = ApprovalSnapshot(
        user_id="user_test_1",
        quote_id=quote.quote_id,
        merchant_id="merchant_test",
        product_id="laptop_test",
        product_version_id=pv.product_version_id,
        quantity=1,
        total_minor=48000,
        currency="USD",
        policy_version=1,
        approved_terms={
            "price_minor": 48000,
            "ram_gb": 16,
            "return_days": 30,
        },
        expires_at=now + timedelta(minutes=15),
        status=ApprovalStatus.ACTIVE,
    )
    db_session.add(approval)
    db_session.commit()
    assert approval.approval_id is not None

    # 5. AuditEvent
    audit = AuditEvent(
        quote_id=quote.quote_id,
        approval_id=approval.approval_id,
        event_type=AuditEventType.APPROVAL_GRANTED,
        payload={"total_minor": 48000, "user_id": "user_test_1"},
    )
    db_session.add(audit)
    db_session.commit()
    assert audit.event_id is not None

    # 6. PaymentAttempt
    attempt = PaymentAttempt(
        user_id="user_test_1",
        approval_id=approval.approval_id,
        total_minor=48000,
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        paypal_order_id="PAYPAL-ORDER-123",
        idempotency_key="idemp_key_123",
    )
    db_session.add(attempt)
    db_session.commit()
    assert attempt.payment_attempt_id is not None


def test_negative_total_minor_rejected(db_session):
    """CHECK constraint: total_minor < 0 must be rejected."""
    pv = ProductVersion(
        product_id="laptop_x",
        version=1,
        merchant_id="m1",
        title="Laptop X",
        price_minor=10000,
        currency="USD",
        ram_gb=8,
        return_days=14,
        policy_version=1,
    )
    db_session.add(pv)
    db_session.commit()

    intent = Intent(
        user_id="user_x",
        raw_text="Need laptop",
        requirements={},
        status=IntentStatus.PENDING,
    )
    db_session.add(intent)
    db_session.commit()

    invalid_quote = Quote(
        user_id="user_x",
        intent_id=intent.intent_id,
        product_version_id=pv.product_version_id,
        merchant_id="m1",
        quantity=1,
        unit_price_minor=10000,
        total_minor=-500,  # Violates CHECK constraint
        currency="USD",
        status=QuoteStatus.ACTIVE,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db_session.add(invalid_quote)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_unique_product_id_and_version_constraint(db_session):
    """UNIQUE(product_id, version) must reject duplicate version entries."""
    pv1 = ProductVersion(
        product_id="laptop_unique",
        version=1,
        merchant_id="m1",
        title="Laptop 1",
        price_minor=40000,
        currency="USD",
        ram_gb=8,
        return_days=14,
        policy_version=1,
    )
    db_session.add(pv1)
    db_session.commit()

    pv2 = ProductVersion(
        product_id="laptop_unique",
        version=1,  # Duplicate version 1
        merchant_id="m1",
        title="Laptop 1 Duplicate",
        price_minor=42000,
        currency="USD",
        ram_gb=8,
        return_days=14,
        policy_version=1,
    )
    db_session.add(pv2)

    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_seed_idempotency_and_laptop_b_specs(db_session):
    """Seeding must create 5 products, and running again must create 0 duplicates."""
    # First seed run
    first_run_inserted = seed_products(db_session)
    assert first_run_inserted == 5

    total_first = db_session.execute(
        select(ProductVersion).where(ProductVersion.is_current.is_(True))
    ).scalars().all()
    assert len(total_first) == 5

    # Second seed run (must be idempotent)
    second_run_inserted = seed_products(db_session)
    assert second_run_inserted == 0

    total_second = db_session.execute(
        select(ProductVersion).where(ProductVersion.is_current.is_(True))
    ).scalars().all()
    assert len(total_second) == 5

    # Verify seed design logic: only laptop_b satisfies price <= 50000, ram >= 16, returns >= 30
    qualifying = [
        p for p in total_second
        if p.price_minor <= 50000 and p.ram_gb >= 16 and p.return_days >= 30
    ]
    assert len(qualifying) == 1
    assert qualifying[0].product_id == "laptop_b"
    assert qualifying[0].price_minor == 48000
    assert qualifying[0].ram_gb == 16
    assert qualifying[0].return_days == 30
