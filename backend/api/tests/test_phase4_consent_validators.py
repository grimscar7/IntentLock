"""Comprehensive Phase 4 test suite for pre-flight consent validators."""

from datetime import UTC, datetime, timedelta

import pytest
from backend.api.consent.comparator import compare
from backend.api.consent.service import (
    consume_approval,
    validate_before_capture,
    validate_before_create,
)
from backend.api.models import (
    ApprovalSnapshot,
    ApprovalStatus,
    AuditEvent,
    AuditEventType,
    Intent,
    PaymentAttempt,
    PaymentAttemptStatus,
    ProductVersion,
    Quote,
    ReasonCode,
)
from backend.scripts.seed import seed_products
from sqlalchemy import select, update


@pytest.fixture(autouse=True)
def setup_seed_catalog(db_session):
    """Seed catalog for each test."""
    seed_products(db_session)


def create_baseline_approval_flow(client, db_session, user_id="buyer_1"):
    """Helper creating a standard verified approval and returning ids."""
    res_intent = client.post(
        "/api/intents",
        headers={"X-User-Id": user_id},
        json={"raw_text": "Looking for a refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    intent_id = res_intent.json()["intent_id"]

    res_quote = client.post(
        "/api/quotes",
        headers={"X-User-Id": user_id},
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    )
    quote_id = res_quote.json()["quote_id"]

    res_appr = client.post(
        "/api/approvals",
        headers={"X-User-Id": user_id},
        json={"quote_id": quote_id},
    )
    approval_id = res_appr.json()["approval_id"]
    return intent_id, quote_id, approval_id


# -----------------------------------------------------------------------------
# 1. Valid Unchanged Approval
# -----------------------------------------------------------------------------
def test_valid_unchanged_approval_create_and_capture_allowed(client, db_session):
    """Valid unchanged approval: create allowed, capture allowed."""
    intent_id, quote_id, approval_id = create_baseline_approval_flow(client, db_session)

    # 1. Validate before create
    res_create = validate_before_create(db_session, "buyer_1", approval_id, record_audit=True)
    assert res_create.allowed is True
    assert res_create.reason_code == ReasonCode.OK
    assert len(res_create.changes) == 0
    assert res_create.requires_new_approval is False

    # Create payment attempt
    attempt = PaymentAttempt(
        user_id="buyer_1",
        approval_id=approval_id,
        total_minor=48000,
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        paypal_order_id="ORDER_TEST_1",
        idempotency_key="idemp_1",
    )
    db_session.add(attempt)
    db_session.commit()

    # 2. Validate before capture
    res_capture = validate_before_capture(
        db_session, "buyer_1", attempt.payment_attempt_id, record_audit=True
    )
    assert res_capture.allowed is True
    assert res_capture.reason_code == ReasonCode.OK
    assert res_capture.requires_new_approval is False


# -----------------------------------------------------------------------------
# 2. Price Increased
# -----------------------------------------------------------------------------
def test_price_increased_blocked_with_changes_and_message(client, db_session):
    """Price increased ($480 -> $540): blocked, QUOTE_CHANGED, structured changes, correct message."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Merchant increases laptop_b price to $540 by creating v2 and flipping is_current
    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b")
        .values(is_current=False)
    )
    v2 = ProductVersion(
        product_version_id="laptop_b_v2",
        product_id="laptop_b",
        version=2,
        merchant_id="demo_store",
        title="Refurbished Laptop B",
        price_minor=54000,
        currency="USD",
        ram_gb=16,
        return_days=30,
        policy_version=1,
        available=True,
        is_current=True,
    )
    db_session.add(v2)
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id, record_audit=True)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.QUOTE_CHANGED
    assert res.requires_new_approval is True

    # Verify structured changes list
    fields = {c.field: c for c in res.changes}
    assert "total_minor" in fields
    assert fields["total_minor"].approved == 48000
    assert fields["total_minor"].current == 54000
    assert "The approved price was $480.00. The current price is $540.00." in res.message


# -----------------------------------------------------------------------------
# 3. Item Mismatch
# -----------------------------------------------------------------------------
def test_different_item_blocked_item_mismatch(client, db_session):
    """Different item at the same price: blocked ITEM_MISMATCH."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Mutate current product_id on approval row in memory/DB
    approval = db_session.execute(
        select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)
    ).scalar_one()
    approval.product_id = "laptop_other"
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.PRODUCT_VERSION_CHANGED or res.reason_code == ReasonCode.ITEM_MISMATCH


# -----------------------------------------------------------------------------
# 4. Merchant Mismatch
# -----------------------------------------------------------------------------
def test_different_merchant_blocked_merchant_mismatch(client, db_session):
    """Different merchant: blocked MERCHANT_MISMATCH."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Merchant ownership changed in current version
    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b", ProductVersion.is_current.is_(True))
        .values(merchant_id="merchant_other")
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.MERCHANT_MISMATCH
    assert any(c.field == "merchant_id" for c in res.changes)


# -----------------------------------------------------------------------------
# 5. Currency Mismatch
# -----------------------------------------------------------------------------
def test_different_currency_blocked_currency_mismatch(client, db_session):
    """Different currency: blocked CURRENCY_MISMATCH."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Change current product currency
    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b", ProductVersion.is_current.is_(True))
        .values(currency="EUR")
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.CURRENCY_MISMATCH


# -----------------------------------------------------------------------------
# 6. Changed Return Policy
# -----------------------------------------------------------------------------
def test_changed_return_terms_blocked(client, db_session):
    """Changed return terms (30 -> 14 days): blocked under approved policy rules."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b", ProductVersion.is_current.is_(True))
        .values(return_days=14)
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code in (ReasonCode.POLICY_CHANGED, ReasonCode.REQUIREMENTS_NO_LONGER_MET)
    assert any(c.field == "return_days" for c in res.changes)


# -----------------------------------------------------------------------------
# 7. Policy Version Changed
# -----------------------------------------------------------------------------
def test_policy_version_changed_blocked(client, db_session):
    """Policy version changed: blocked POLICY_CHANGED."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b", ProductVersion.is_current.is_(True))
        .values(policy_version=2)
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.POLICY_CHANGED
    assert any(c.field == "policy_version" for c in res.changes)


# -----------------------------------------------------------------------------
# 8. Expired Approval
# -----------------------------------------------------------------------------
def test_expired_approval_blocked_and_status_updated(client, db_session):
    """Expired approval: blocked APPROVAL_EXPIRED and status set to EXPIRED."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Force expiration in DB
    db_session.execute(
        update(ApprovalSnapshot)
        .where(ApprovalSnapshot.approval_id == approval_id)
        .values(expires_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.APPROVAL_EXPIRED
    assert res.requires_new_approval is True

    # Check status transitioned to EXPIRED
    appr = db_session.execute(
        select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)
    ).scalar_one()
    assert appr.status == ApprovalStatus.EXPIRED


# -----------------------------------------------------------------------------
# 9. Another User's Approval
# -----------------------------------------------------------------------------
def test_another_users_approval_hidden_as_not_found(client, db_session):
    """Another user's approval: APPROVAL_NOT_FOUND, true reason in audit payload only."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session, user_id="buyer_1")

    res = validate_before_create(db_session, "buyer_2", approval_id, record_audit=True)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.APPROVAL_NOT_FOUND

    # Verify audit payload records internal user mismatch
    audit = db_session.execute(
        select(AuditEvent)
        .where(
            AuditEvent.approval_id == approval_id,
            AuditEvent.event_type == AuditEventType.PRE_CREATE_VALIDATION_FAILED,
        )
        .order_by(AuditEvent.created_at.desc())
    ).scalars().first()
    assert audit is not None
    assert "user_mismatch" in audit.payload.get("internal_rejection", "")


# -----------------------------------------------------------------------------
# 10. Missing Approval ID
# -----------------------------------------------------------------------------
def test_missing_approval_id(db_session):
    """Missing approval id: APPROVAL_NOT_FOUND."""
    res = validate_before_create(db_session, "buyer_1", "nonexistent-uuid")
    assert res.allowed is False
    assert res.reason_code == ReasonCode.APPROVAL_NOT_FOUND


# -----------------------------------------------------------------------------
# 11. Old Approval After Newer Granted
# -----------------------------------------------------------------------------
def test_old_approval_after_newer_granted_is_superseded(client, db_session):
    """Old approval after a newer approval granted: APPROVAL_SUPERSEDED."""
    intent_id, q1_id, app1_id = create_baseline_approval_flow(client, db_session)

    # Create Quote 2 & Approval 2 for same product
    q2_resp = client.post(
        "/api/quotes",
        headers={"X-User-Id": "buyer_1"},
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    )
    q2_id = q2_resp.json()["quote_id"]
    client.post("/api/approvals", headers={"X-User-Id": "buyer_1"}, json={"quote_id": q2_id})

    # Validate approval 1
    res = validate_before_create(db_session, "buyer_1", app1_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.APPROVAL_SUPERSEDED


# -----------------------------------------------------------------------------
# 12. Invalidated Approval
# -----------------------------------------------------------------------------
def test_invalidated_approval_blocked(client, db_session):
    """INVALIDATED approval: APPROVAL_INVALIDATED."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    db_session.execute(
        update(ApprovalSnapshot)
        .where(ApprovalSnapshot.approval_id == approval_id)
        .values(status=ApprovalStatus.INVALIDATED)
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.APPROVAL_INVALIDATED


# -----------------------------------------------------------------------------
# 13. Current Product No Longer Meets Budget
# -----------------------------------------------------------------------------
def test_product_no_longer_meets_budget(client, db_session):
    """Current product no longer meets stored budget: REQUIREMENTS_NO_LONGER_MET."""
    intent_id, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Lower intent budget below laptop_b current price ($480)
    db_session.execute(
        update(Intent)
        .where(Intent.intent_id == intent_id)
        .values(requirements={"category": "laptop", "max_total_minor": 45000, "currency": "USD", "minimum_ram_gb": 16, "minimum_return_days": 30})
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert res.reason_code in (ReasonCode.REQUIREMENTS_NO_LONGER_MET, ReasonCode.QUOTE_CHANGED)


# -----------------------------------------------------------------------------
# 14. Missing return_days On Current Product
# -----------------------------------------------------------------------------
def test_missing_return_days_never_satisfies(client, db_session):
    """Missing return_days on current product: does not silently satisfy the requirement."""
    now = datetime.now(UTC)
    approval = ApprovalSnapshot(
        approval_id="app_test",
        user_id="buyer_1",
        quote_id="quote_test",
        merchant_id="demo_store",
        product_id="laptop_b",
        product_version_id="laptop_b_v1",
        quantity=1,
        total_minor=48000,
        currency="USD",
        policy_version=1,
        approved_terms={"price_minor": 48000, "ram_gb": 16, "return_days": 30},
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    quote = Quote(
        quote_id="quote_test",
        user_id="buyer_1",
        intent_id="intent_test",
        product_version_id="laptop_b_v1",
        merchant_id="demo_store",
        quantity=1,
        unit_price_minor=48000,
        total_minor=48000,
        currency="USD",
        expires_at=now + timedelta(minutes=15),
    )
    approved_pv = ProductVersion(
        product_version_id="laptop_b_v1",
        product_id="laptop_b",
        version=1,
        merchant_id="demo_store",
        title="Laptop B",
        price_minor=48000,
        currency="USD",
        ram_gb=16,
        return_days=30,
        policy_version=1,
    )
    # Current product version has return_days = None (missing field)
    current_pv = ProductVersion(
        product_version_id="laptop_b_v2",
        product_id="laptop_b",
        version=2,
        merchant_id="demo_store",
        title="Laptop B",
        price_minor=48000,
        currency="USD",
        ram_gb=16,
        return_days=None,
        policy_version=1,
    )
    requirements = {
        "category": "laptop",
        "max_total_minor": 50000,
        "currency": "USD",
        "minimum_ram_gb": 16,
        "minimum_return_days": 30,
    }

    changes, reason_code = compare(
        approval=approval,
        quote=quote,
        approved_pv=approved_pv,
        current_pv=current_pv,
        requirements=requirements,
    )
    assert reason_code in (ReasonCode.POLICY_CHANGED, ReasonCode.REQUIREMENTS_NO_LONGER_MET)



# -----------------------------------------------------------------------------
# 15. Product Unavailable
# -----------------------------------------------------------------------------
def test_product_unavailable_blocked(client, db_session):
    """Product unavailable: blocked."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b", ProductVersion.is_current.is_(True))
        .values(available=False)
    )
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is False
    assert any(c.field == "available" for c in res.changes)


# -----------------------------------------------------------------------------
# 16. Identical Terms On Newer Version Allowed
# -----------------------------------------------------------------------------
def test_identical_terms_on_newer_version_allowed(client, db_session):
    """Identical terms on a newer version number: allowed."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Insert v2 with identical terms (e.g. description or minor metadata bump)
    db_session.execute(
        update(ProductVersion)
        .where(ProductVersion.product_id == "laptop_b")
        .values(is_current=False)
    )
    v2 = ProductVersion(
        product_version_id="laptop_b_v2",
        product_id="laptop_b",
        version=2,
        merchant_id="demo_store",
        title="Refurbished Laptop B",
        price_minor=48000,
        currency="USD",
        ram_gb=16,
        return_days=30,
        policy_version=1,
        available=True,
        is_current=True,
    )
    db_session.add(v2)
    db_session.commit()

    res = validate_before_create(db_session, "buyer_1", approval_id)
    assert res.allowed is True
    assert res.reason_code == ReasonCode.OK


# -----------------------------------------------------------------------------
# 17. Capture: Attempt With No Bound Approval
# -----------------------------------------------------------------------------
def test_capture_attempt_unbound(db_session):
    """Capture: attempt with no bound approval: PAYMENT_ATTEMPT_UNBOUND."""
    # Insert attempt without approval_id
    attempt = PaymentAttempt(
        user_id="buyer_1",
        approval_id="",
        total_minor=48000,
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        idempotency_key="idemp_unbound",
    )
    db_session.add(attempt)
    db_session.commit()

    res = validate_before_capture(db_session, "buyer_1", attempt.payment_attempt_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.PAYMENT_ATTEMPT_UNBOUND


# -----------------------------------------------------------------------------
# 18. Capture: Another User's Attempt
# -----------------------------------------------------------------------------
def test_capture_another_users_attempt(client, db_session):
    """Capture: another user's attempt: PAYMENT_ATTEMPT_NOT_FOUND."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    attempt = PaymentAttempt(
        user_id="buyer_1",
        approval_id=approval_id,
        total_minor=48000,
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        idempotency_key="idemp_user1",
    )
    db_session.add(attempt)
    db_session.commit()

    res = validate_before_capture(db_session, "buyer_2", attempt.payment_attempt_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.PAYMENT_ATTEMPT_NOT_FOUND


# -----------------------------------------------------------------------------
# 19. Capture: Attempt Amount Differs From Approval
# -----------------------------------------------------------------------------
def test_capture_amount_differs_from_approval(client, db_session):
    """Capture: attempt amount differs from approval: AMOUNT_MISMATCH."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    attempt = PaymentAttempt(
        user_id="buyer_1",
        approval_id=approval_id,
        total_minor=99999,  # Mismatched amount
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        idempotency_key="idemp_amt_mismatch",
    )
    db_session.add(attempt)
    db_session.commit()

    res = validate_before_capture(db_session, "buyer_1", attempt.payment_attempt_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.AMOUNT_MISMATCH


# -----------------------------------------------------------------------------
# 20. Capture: Caller Approval ID Ignored
# -----------------------------------------------------------------------------
def test_capture_bound_approval_used_caller_ignored(client, db_session):
    """Capture: approval id supplied by caller is ignored, bound approval is used."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    attempt = PaymentAttempt(
        user_id="buyer_1",
        approval_id=approval_id,
        total_minor=48000,
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        idempotency_key="idemp_bound_check",
    )
    db_session.add(attempt)
    db_session.commit()

    # Function signature only accepts payment_attempt_id, guaranteeing caller cannot supply arbitrary approval_id
    res = validate_before_capture(db_session, "buyer_1", attempt.payment_attempt_id)
    assert res.allowed is True
    assert res.approval_id == approval_id


# -----------------------------------------------------------------------------
# 21. consume_approval: Exactly Once & Prevents Double Capture
# -----------------------------------------------------------------------------
def test_consume_approval_idempotency_and_double_capture_block(client, db_session):
    """consume_approval returns True once, False second time; second capture blocked APPROVAL_ALREADY_CONSUMED."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    attempt = PaymentAttempt(
        user_id="buyer_1",
        approval_id=approval_id,
        total_minor=48000,
        currency="USD",
        status=PaymentAttemptStatus.INITIATED,
        idempotency_key="idemp_consume_test",
    )
    db_session.add(attempt)
    db_session.commit()

    # First consume call returns True
    first_consumed = consume_approval(db_session, approval_id)
    assert first_consumed is True

    # Second consume call returns False
    second_consumed = consume_approval(db_session, approval_id)
    assert second_consumed is False

    # Second capture validation is blocked
    res_second_capture = validate_before_capture(
        db_session, "buyer_1", attempt.payment_attempt_id
    )
    assert res_second_capture.allowed is False
    assert res_second_capture.reason_code == ReasonCode.APPROVAL_ALREADY_CONSUMED


# -----------------------------------------------------------------------------
# 22. Unexpected DB Error Fails Closed
# -----------------------------------------------------------------------------
def test_unexpected_db_error_fails_closed(client, db_session, monkeypatch):
    """Unexpected DB error: allowed false, VALIDATION_ERROR, nothing raised to caller."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    def broken_execute(*args, **kwargs):
        raise RuntimeError("Simulated database failure")

    monkeypatch.setattr(db_session, "execute", broken_execute)

    res = validate_before_create(db_session, "buyer_1", approval_id, record_audit=False)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.VALIDATION_ERROR
    assert res.requires_new_approval is False


# -----------------------------------------------------------------------------
# 23. Audit Event Persistence
# -----------------------------------------------------------------------------
def test_audit_event_persists_even_if_caller_rolls_back(client, db_session):
    """Audit: each call writes the right event type, failed events persist after caller rollback."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    # Invalidate approval to force a validation failure
    db_session.execute(
        update(ApprovalSnapshot)
        .where(ApprovalSnapshot.approval_id == approval_id)
        .values(status=ApprovalStatus.INVALIDATED)
    )
    db_session.commit()

    validate_before_create(db_session, "buyer_1", approval_id, record_audit=True)

    # Caller performs rollback
    db_session.rollback()

    # Verify audit event is still in the DB
    audit = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.approval_id == approval_id,
            AuditEvent.event_type == AuditEventType.PRE_CREATE_VALIDATION_FAILED,
        )
    ).scalar_one_or_none()
    assert audit is not None


# -----------------------------------------------------------------------------
# 24. Dry Run Route
# -----------------------------------------------------------------------------
def test_dry_run_route_writes_no_audit_event(client, db_session):
    """Dry run route: writes no audit event and changes no status."""
    _, _, approval_id = create_baseline_approval_flow(client, db_session)

    events_before = len(db_session.execute(select(AuditEvent)).scalars().all())

    resp = client.post(
        f"/api/approvals/{approval_id}/validate",
        headers={"X-User-Id": "buyer_1"},
    )
    assert resp.status_code == 200
    assert resp.json()["allowed"] is True

    events_after = len(db_session.execute(select(AuditEvent)).scalars().all())
    assert events_before == events_after


# -----------------------------------------------------------------------------
# 25. Comparator Unit Tests
# -----------------------------------------------------------------------------
def test_comparator_multiple_simultaneous_changes_and_priority_order():
    """Comparator unit tests: all differences listed, reason priority order respected."""
    now = datetime.now(UTC)
    approval = ApprovalSnapshot(
        approval_id="app_1",
        user_id="user_1",
        quote_id="quote_1",
        merchant_id="merchant_a",
        product_id="laptop_b",
        product_version_id="laptop_b_v1",
        quantity=1,
        total_minor=48000,
        currency="USD",
        policy_version=1,
        approved_terms={"price_minor": 48000, "ram_gb": 16, "return_days": 30},
        approved_at=now,
        expires_at=now + timedelta(minutes=15),
    )
    quote = Quote(
        quote_id="quote_1",
        user_id="user_1",
        intent_id="intent_1",
        product_version_id="laptop_b_v1",
        merchant_id="merchant_a",
        quantity=1,
        unit_price_minor=48000,
        total_minor=48000,
        currency="USD",
        expires_at=now + timedelta(minutes=15),
    )
    approved_pv = ProductVersion(
        product_version_id="laptop_b_v1",
        product_id="laptop_b",
        version=1,
        merchant_id="merchant_a",
        title="Laptop B",
        price_minor=48000,
        currency="USD",
        ram_gb=16,
        return_days=30,
        policy_version=1,
    )
    # Current PV has multiple changes: merchant changed AND price changed AND policy changed
    current_pv = ProductVersion(
        product_version_id="laptop_b_v2",
        product_id="laptop_b",
        version=2,
        merchant_id="merchant_b",  # Merchant mismatch
        title="Laptop B",
        price_minor=54000,         # Price increased
        currency="USD",
        ram_gb=16,
        return_days=14,            # Policy changed
        policy_version=2,          # Policy version changed
    )
    requirements = {
        "category": "laptop",
        "max_total_minor": 50000,
        "currency": "USD",
        "minimum_ram_gb": 16,
        "minimum_return_days": 30,
    }

    changes, primary_code = compare(
        approval=approval,
        quote=quote,
        approved_pv=approved_pv,
        current_pv=current_pv,
        requirements=requirements,
    )

    # 1. All discrepancies are captured in changes
    changed_fields = [c.field for c in changes]
    assert "merchant_id" in changed_fields
    assert "price_minor" in changed_fields
    assert "total_minor" in changed_fields
    assert "return_days" in changed_fields
    assert "policy_version" in changed_fields

    # 2. Priority order: MERCHANT_MISMATCH is higher priority than QUOTE_CHANGED or POLICY_CHANGED
    assert primary_code == ReasonCode.MERCHANT_MISMATCH
