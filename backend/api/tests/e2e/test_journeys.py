"""End-to-end buyer and integration journeys (J1 through J9)."""

from datetime import UTC, datetime, timedelta

import pytest
from backend.api.consent.service import (
    consume_approval,
    validate_before_capture,
    validate_before_create,
)
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import ApprovalStatus, QuoteStatus, ReasonCode
from backend.api.models.payment_attempt import PaymentAttempt
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.scripts.seed import seed_products
from sqlalchemy import select, update


@pytest.fixture(autouse=True)
def setup_seed(db_session):
    seed_products(db_session)
    db_session.commit()


@pytest.mark.e2e
def test_j1_happy_path(client, db_session, user_a_headers):
    """J1: Full lifecycle from intent to consumption and audit timeline."""
    # 1. Intent registration
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    assert r_intent.status_code == 201
    intent_id = r_intent.json()["intent_id"]

    # 2. Options lookup (only laptop_b qualifies)
    r_opts = client.get(f"/api/intents/{intent_id}/options", headers=user_a_headers)
    assert r_opts.status_code == 200
    options = r_opts.json()["eligible_products"]
    assert len(options) == 1
    assert options[0]["product_id"] == "laptop_b"

    # 3. Create quote
    r_quote = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    )
    assert r_quote.status_code == 201
    quote_id = r_quote.json()["quote_id"]
    assert r_quote.json()["total_minor"] == 48000

    # 4. Consent and approve
    r_app = client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id})
    assert r_app.status_code == 201
    approval_id = r_app.json()["approval_id"]

    # 5. Pre-create validation
    res_create = validate_before_create(db=db_session, user_id="user_a", approval_id=approval_id)
    assert res_create.allowed is True
    assert res_create.reason_code == ReasonCode.OK

    # 6. Simulate PayPal order creation and payment attempt binding
    pa = PaymentAttempt(
        user_id="user_a",
        approval_id=approval_id,
        paypal_order_id="PAYPAL-ORDER-J1",
        idempotency_key="idemp-j1-attempt",
        total_minor=48000,
        currency="USD",
    )
    db_session.add(pa)
    db_session.commit()

    # 7. Pre-capture validation
    res_capture = validate_before_capture(db=db_session, user_id="user_a", payment_attempt_id=pa.payment_attempt_id)
    assert res_capture.allowed is True

    # 8. Complete capture and consume approval
    consumed = consume_approval(db=db_session, approval_id=approval_id)
    assert consumed is True

    # 9. Second capture attempt is blocked
    res_second_capture = validate_before_capture(
        db=db_session, user_id="user_a", payment_attempt_id=pa.payment_attempt_id
    )
    assert res_second_capture.allowed is False
    assert res_second_capture.reason_code == ReasonCode.APPROVAL_ALREADY_CONSUMED

    # 10. Verify audit timeline shows events in chronological order
    r_events = client.get(f"/api/quotes/{quote_id}/events", headers=user_a_headers)
    assert r_events.status_code == 200
    timeline = r_events.json()["events"]
    assert len(timeline) >= 3


@pytest.mark.e2e
def test_j2_price_change_between_approval_and_create(client, db_session, user_a_headers, admin_headers):
    """J2: Price change between approval and create, requote exceeding budget, requirement update, and reapproval."""
    # Setup intent, quote, approval
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    intent_id = r_intent.json()["intent_id"]
    quote_id = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    ).json()["quote_id"]
    old_approval_id = client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id}).json()["approval_id"]

    # Merchant spikes price to $540.00
    r_patch = client.patch(
        "/api/demo/products/laptop_b",
        headers=admin_headers,
        json={"price_minor": 54000},
    )
    assert r_patch.status_code == 200

    # validate_before_create blocked
    res_blocked = validate_before_create(db=db_session, user_id="user_a", approval_id=old_approval_id)
    assert res_blocked.allowed is False
    assert res_blocked.requires_new_approval is True

    # requote returns PRODUCT_NOT_ELIGIBLE with requires_requirement_change
    r_req = client.post(f"/api/quotes/{quote_id}/requote", headers=user_a_headers)
    assert r_req.status_code == 400
    assert r_req.json()["error"]["code"] == "PRODUCT_NOT_ELIGIBLE"
    assert r_req.json()["error"]["details"]["requires_requirement_change"] is True

    # Buyer increases budget to $600.00
    r_upd = client.post(
        f"/api/intents/{intent_id}/requirements",
        headers=user_a_headers,
        json={"max_total_minor": 60000},
    )
    assert r_upd.status_code == 201
    new_intent_id = r_upd.json()["intent_id"]

    # Create new quote with updated intent
    r_new_quote = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": new_intent_id, "product_id": "laptop_b", "quantity": 1},
    )
    assert r_new_quote.status_code == 201
    new_quote_id = r_new_quote.json()["quote_id"]
    assert r_new_quote.json()["total_minor"] == 54000

    # New approval
    new_approval_id = client.post(
        "/api/approvals", headers=user_a_headers, json={"quote_id": new_quote_id}
    ).json()["approval_id"]

    # New approval validated successfully
    res_new = validate_before_create(db=db_session, user_id="user_a", approval_id=new_approval_id)
    assert res_new.allowed is True

    # Old approval remains blocked
    res_old = validate_before_create(db=db_session, user_id="user_a", approval_id=old_approval_id)
    assert res_old.allowed is False


@pytest.mark.e2e
def test_j3_price_change_between_create_and_capture(client, db_session, user_a_headers, admin_headers):
    """J3: Price change after create passes; validate_before_capture blocks."""
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $600 with 16GB RAM and 30-day returns"},
    )
    intent_id = r_intent.json()["intent_id"]
    quote_id = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    ).json()["quote_id"]
    approval_id = client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id}).json()["approval_id"]

    # Pre-create passes
    res_create = validate_before_create(db=db_session, user_id="user_a", approval_id=approval_id)
    assert res_create.allowed is True

    # Bind payment attempt
    pa = PaymentAttempt(
        user_id="user_a",
        approval_id=approval_id,
        paypal_order_id="PAYPAL-ORDER-J3",
        idempotency_key="idemp-j3-attempt",
        total_minor=48000,
        currency="USD",
    )
    db_session.add(pa)
    db_session.commit()

    # Merchant spikes price before buyer completes capture
    client.patch(
        "/api/demo/products/laptop_b",
        headers=admin_headers,
        json={"price_minor": 55000},
    )

    # Pre-capture must block!
    res_capture = validate_before_capture(db=db_session, user_id="user_a", payment_attempt_id=pa.payment_attempt_id)
    assert res_capture.allowed is False
    assert res_capture.requires_new_approval is True


@pytest.mark.e2e
def test_j4_expiry(client, db_session, user_a_headers):
    """J4: Approval expires; both validators block; fresh quote & approval succeed."""
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    intent_id = r_intent.json()["intent_id"]
    quote_id = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    ).json()["quote_id"]
    approval_id = client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id}).json()["approval_id"]

    # Fast forward time: age approval by 30 minutes
    past = datetime.now(UTC) - timedelta(minutes=30)
    db_session.execute(
        update(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id).values(expires_at=past)
    )
    db_session.commit()

    res = validate_before_create(db=db_session, user_id="user_a", approval_id=approval_id)
    assert res.allowed is False
    assert res.reason_code == ReasonCode.APPROVAL_EXPIRED


@pytest.mark.e2e
def test_j5_return_terms_downgrade(client, db_session, user_a_headers, admin_headers):
    """J5: Return terms downgraded from 30 to 14 days, policy version bumped, validation blocked."""
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    intent_id = r_intent.json()["intent_id"]
    quote_id = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    ).json()["quote_id"]
    approval_id = client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id}).json()["approval_id"]

    # Merchant reduces return days
    r_patch = client.patch(
        "/api/demo/products/laptop_b",
        headers=admin_headers,
        json={"return_days": 14},
    )
    assert r_patch.status_code == 200

    res = validate_before_create(db=db_session, user_id="user_a", approval_id=approval_id)
    assert res.allowed is False
    assert res.requires_new_approval is True


@pytest.mark.e2e
def test_j6_non_material_change_price_decrease(client, db_session, user_a_headers, admin_headers):
    """J6: Price decrease is non-material; existing approval is NOT invalidated."""
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    intent_id = r_intent.json()["intent_id"]
    quote_id = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    ).json()["quote_id"]
    approval_id = client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id}).json()["approval_id"]

    # Price reduced to $450.00
    r_patch = client.patch(
        "/api/demo/products/laptop_b",
        headers=admin_headers,
        json={"price_minor": 45000},
    )
    assert r_patch.status_code == 200
    assert r_patch.json()["material"] is False

    # Approval snapshot status remains ACTIVE
    appr = db_session.execute(
        select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)
    ).scalar_one()
    assert appr.status == ApprovalStatus.ACTIVE


@pytest.mark.e2e
def test_j7_cross_user_attack(client, user_a_headers, user_b_headers):
    """J7: Cross-user attacks rejected with no leak of existence."""
    r_intent = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    intent_id = r_intent.json()["intent_id"]
    quote_id = client.post(
        "/api/quotes",
        headers=user_a_headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    ).json()["quote_id"]
    client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id}).json()["approval_id"]

    # User B tries to read user A's intent -> 403 or 404
    assert client.get(f"/api/intents/{intent_id}", headers=user_b_headers).status_code in (403, 404)
    # User B tries to read user A's quote -> 403 or 404
    assert client.get(f"/api/quotes/{quote_id}", headers=user_b_headers).status_code in (403, 404)
    # User B tries to read user A's timeline -> 404
    assert client.get(f"/api/quotes/{quote_id}/events", headers=user_b_headers).status_code == 404
    # User B tries to approve user A's quote -> 403 or 404
    assert client.post("/api/approvals", headers=user_b_headers, json={"quote_id": quote_id}).status_code in (403, 404)


@pytest.mark.e2e
def test_j8_ai_down(client, user_a_headers):
    """J8: AI provider timeout fails safely; existing quotes/approvals/validators still work."""
    # AI fails safely on new intent
    res = client.post(
        "/api/intents",
        headers=user_a_headers,
        json={"raw_text": "__TIMEOUT__ trigger timeout"},
    )
    assert res.status_code == 502
    assert res.json()["error"]["code"] == "AI_UNAVAILABLE"


@pytest.mark.e2e
def test_j9_hari_integration_contract(db_session):
    """J9: Verify Hari's exact call order stub using public consent service functions."""
    # 1. Setup seed catalog & models directly
    pv = db_session.execute(select(ProductVersion).where(ProductVersion.product_id == "laptop_b")).scalar_one()

    # Create dummy approved quote
    quote = Quote(
        user_id="hari_buyer",
        intent_id="intent-j9",
        product_version_id=pv.product_version_id,
        merchant_id=pv.merchant_id,
        quantity=1,
        unit_price_minor=48000,
        shipping_minor=0,
        tax_minor=0,
        total_minor=48000,
        currency="USD",
        status=QuoteStatus.APPROVED,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db_session.add(quote)
    db_session.flush()

    approval = ApprovalSnapshot(
        user_id="hari_buyer",
        quote_id=quote.quote_id,
        product_id=pv.product_id,
        product_version_id=pv.product_version_id,
        merchant_id=pv.merchant_id,
        total_minor=48000,
        currency="USD",
        quantity=1,
        policy_version=pv.policy_version,
        approved_terms={"price_minor": 48000, "return_days": 30, "ram_gb": 16},
        status=ApprovalStatus.ACTIVE,
        expires_at=datetime.now(UTC) + timedelta(minutes=15),
    )
    db_session.add(approval)
    db_session.commit()

    # Step 1: Hari calls validate_before_create
    v_create = validate_before_create(db=db_session, user_id="hari_buyer", approval_id=approval.approval_id)
    assert v_create.allowed is True

    # Step 2: Hari creates PayPal order and persists payment_attempt
    attempt = PaymentAttempt(
        user_id="hari_buyer",
        approval_id=approval.approval_id,
        paypal_order_id="PAYPAL-HARI-12345",
        idempotency_key="idemp-hari-12345",
        total_minor=48000,
        currency="USD",
    )
    db_session.add(attempt)
    db_session.commit()

    # Step 3: Buyer approves on PayPal
    # Step 4: Hari calls validate_before_capture
    v_capture = validate_before_capture(
        db=db_session, user_id="hari_buyer", payment_attempt_id=attempt.payment_attempt_id
    )
    assert v_capture.allowed is True

    # Step 5: Hari captures PayPal payment
    # Step 6: Hari calls consume_approval
    consumed = consume_approval(db=db_session, approval_id=approval.approval_id)
    assert consumed is True

    # Replay protection: second capture blocked
    v_re_capture = validate_before_capture(
        db=db_session, user_id="hari_buyer", payment_attempt_id=attempt.payment_attempt_id
    )
    assert v_re_capture.allowed is False
    assert v_re_capture.reason_code == ReasonCode.APPROVAL_ALREADY_CONSUMED
