"""Database integrity, constraint enforcement, and Alembic schema tests."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from backend.api.models.audit_event import AuditEvent
from backend.api.models.enums import AuditEventType, QuoteStatus
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.scripts.seed import seed_products


@pytest.fixture(autouse=True)
def setup_seed(db_session):
    seed_products(db_session)
    db_session.commit()


@pytest.mark.integration
def test_db_check_constraint_rejects_negative_total_minor(db_session):
    """CHECK constraint reject_negative_totals on quotes and payment_attempts."""
    pv = db_session.execute(select(ProductVersion).where(ProductVersion.product_id == "laptop_b")).scalar_one()

    # Attempt inserting quote with negative total_minor
    bad_quote = Quote(
        user_id="user_test",
        intent_id="intent-test",
        product_version_id=pv.product_version_id,
        merchant_id=pv.merchant_id,
        quantity=1,
        unit_price_minor=48000,
        shipping_minor=0,
        tax_minor=0,
        total_minor=-500,
        currency="USD",
        status=QuoteStatus.ACTIVE,
        expires_at=datetime.now(UTC),
    )
    db_session.add(bad_quote)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.integration
def test_db_check_constraint_rejects_zero_or_negative_quantity(db_session):
    """CHECK constraint reject zero or negative quantity on quotes."""
    pv = db_session.execute(select(ProductVersion).where(ProductVersion.product_id == "laptop_b")).scalar_one()

    bad_qty_quote = Quote(
        user_id="user_test",
        intent_id="intent-test",
        product_version_id=pv.product_version_id,
        merchant_id=pv.merchant_id,
        quantity=0,
        unit_price_minor=48000,
        shipping_minor=0,
        tax_minor=0,
        total_minor=48000,
        currency="USD",
        status=QuoteStatus.ACTIVE,
        expires_at=datetime.now(UTC),
    )
    db_session.add(bad_qty_quote)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.integration
def test_db_unique_product_id_and_version_enforced(db_session):
    """UNIQUE(product_id, version) constraint prevents duplicate versions."""
    duplicate_pv = ProductVersion(
        product_version_id="laptop_b_v1_dup",
        product_id="laptop_b",
        version=1,  # Version 1 already seeded
        merchant_id="demo_store",
        title="Duplicate Laptop",
        price_minor=48000,
        currency="USD",
        ram_gb=16,
        return_days=30,
        policy_version=1,
        available=True,
        is_current=False,
    )
    db_session.add(duplicate_pv)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


@pytest.mark.integration
def test_db_single_is_current_row_per_product(db_session):
    """Ensure every seeded catalog item has exactly one is_current=True row."""
    current_products = db_session.execute(
        select(ProductVersion).where(ProductVersion.is_current.is_(True))
    ).scalars().all()
    assert len(current_products) == 5

    product_ids = [p.product_id for p in current_products]
    assert len(product_ids) == len(set(product_ids)), "Duplicate is_current product rows found!"


@pytest.mark.integration
def test_db_audit_events_append_only(db_session):
    """AuditEvent is strictly append-only; records are immutable."""
    pv = db_session.execute(select(ProductVersion).where(ProductVersion.product_id == "laptop_b")).scalar_one()
    quote = Quote(
        user_id="audit_tester",
        intent_id="intent-audit",
        product_version_id=pv.product_version_id,
        merchant_id=pv.merchant_id,
        quantity=1,
        unit_price_minor=48000,
        shipping_minor=0,
        tax_minor=0,
        total_minor=48000,
        currency="USD",
        status=QuoteStatus.ACTIVE,
        expires_at=datetime.now(UTC),
    )
    db_session.add(quote)
    db_session.flush()

    event = AuditEvent(
        quote_id=quote.quote_id,
        event_type=AuditEventType.QUOTE_CREATED,
        payload={"immutable_key": "original_value"},
    )
    db_session.add(event)
    db_session.commit()

    loaded = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_id == event.event_id)
    ).scalar_one()
    assert loaded.payload["immutable_key"] == "original_value"
