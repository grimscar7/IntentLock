"""Tests for Phase 5: Demo merchant changes, invalidation, reapproval, and timeline."""

from datetime import datetime

import pytest
from sqlalchemy import select

from backend.api.ai.service import FakeAIClient, validate_and_fallback_reword
from backend.api.audit.service import record_event
from backend.api.config import get_settings
from backend.api.consent.service import consume_approval, create_approval
from backend.api.intents.service import create_intent
from backend.api.models.audit_event import AuditEvent
from backend.api.models.enums import ApprovalStatus, AuditEventType, QuoteStatus
from backend.api.models.product_version import ProductVersion
from backend.api.quotes.service import create_quote
from backend.scripts.seed import seed_products

ADMIN_TOKEN = get_settings().DEMO_ADMIN_TOKEN
ADMIN_HEADERS = {"X-Demo-Admin-Token": ADMIN_TOKEN}
USER_HEADERS = {"X-User-Id": "test_buyer"}
OTHER_USER_HEADERS = {"X-User-Id": "other_buyer"}


@pytest.fixture(autouse=True)
def setup_seed(db_session):
    """Ensure catalog products are seeded before each test."""
    seed_products(db_session)
    db_session.commit()


def _setup_intent_and_quote(client, db_session, price_limit=50000):
    """Helper creating a validated intent and an active quote for laptop_b ($480)."""
    fake_ai = FakeAIClient()
    intent, _ = create_intent(
        user_id="test_buyer",
        raw_text=f"Refurbished laptop under ${price_limit // 100} with 16GB RAM and 30-day returns",
        db=db_session,
        ai_client=fake_ai,
    )
    quote = create_quote(
        user_id="test_buyer",
        intent_id=intent.intent_id,
        product_id="laptop_b",
        quantity=1,
        db=db_session,
    )
    return intent, quote


# 1. Reject PATCH without admin token
def test_demo_patch_rejects_missing_admin_token(client):
    res = client.patch("/api/demo/products/laptop_b", json={"price_minor": 55000})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


# 2. Reject PATCH with wrong admin token
def test_demo_patch_rejects_wrong_admin_token(client):
    res = client.patch(
        "/api/demo/products/laptop_b",
        headers={"X-Demo-Admin-Token": "wrong_secret"},
        json={"price_minor": 55000},
    )
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "FORBIDDEN"


# 3. Reject invalid field in demo PATCH
def test_demo_patch_rejects_invalid_field(client):
    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"cpu": "intel_i7"},
    )
    assert res.status_code in (400, 422)


# 4. Reject negative price in demo PATCH
def test_demo_patch_rejects_negative_price(client):
    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": -500},
    )
    assert res.status_code in (400, 422)


# 5. Price increase creates new ProductVersion
def test_demo_patch_price_increase_creates_new_version(client, db_session):
    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 55000},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["product_id"] == "laptop_b"
    assert data["new_version"] == 2
    assert data["material"] is True

    # Check database versions
    v1 = db_session.execute(
        select(ProductVersion).where(ProductVersion.product_id == "laptop_b", ProductVersion.version == 1)
    ).scalar_one()
    v2 = db_session.execute(
        select(ProductVersion).where(ProductVersion.product_id == "laptop_b", ProductVersion.version == 2)
    ).scalar_one()
    assert v1.is_current is False
    assert v2.is_current is True


# 6. Price increase invalidates active quotes
def test_price_increase_invalidates_active_quotes(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    assert quote.status == QuoteStatus.ACTIVE

    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 55000},
    )
    assert res.status_code == 200
    assert res.json()["invalidated_quotes"] >= 1

    db_session.refresh(quote)
    assert quote.status == QuoteStatus.INVALIDATED


# 7. Price increase invalidates active approvals
def test_price_increase_invalidates_active_approvals(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    approval = create_approval(user_id="test_buyer", quote_id=quote.quote_id, db=db_session)
    assert approval.status == ApprovalStatus.ACTIVE

    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 55000},
    )
    assert res.status_code == 200
    assert res.json()["invalidated_approvals"] >= 1

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.INVALIDATED


# 8. Price increase records audit events
def test_price_increase_records_audit_events(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    approval = create_approval(user_id="test_buyer", quote_id=quote.quote_id, db=db_session)

    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 55000},
    )
    assert res.status_code == 200

    pvc_events = db_session.execute(
        select(AuditEvent).where(AuditEvent.event_type == AuditEventType.PRODUCT_VERSION_CHANGED)
    ).scalars().all()
    assert len(pvc_events) >= 1

    ai_events = db_session.execute(
        select(AuditEvent).where(
            AuditEvent.event_type == AuditEventType.APPROVAL_INVALIDATED,
            AuditEvent.approval_id == approval.approval_id,
        )
    ).scalars().all()
    assert len(ai_events) == 1


# 9. Price decrease does not invalidate approvals (non-material)
def test_price_decrease_does_not_invalidate_approvals(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    approval = create_approval(user_id="test_buyer", quote_id=quote.quote_id, db=db_session)

    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 45000},  # Down from 48000
    )
    assert res.status_code == 200
    assert res.json()["material"] is False
    assert res.json()["invalidated_approvals"] == 0

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.ACTIVE


# 10. No-op PATCH with same price
def test_demo_patch_noop_same_price(client, db_session):
    res = client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 48000},
    )
    assert res.status_code == 200
    data = res.json()
    assert data["new_version"] == 1
    assert data["old_version"] == 1
    assert data["changes"] == []


# 11. Consumed approvals are NEVER invalidated
def test_consumed_approvals_never_invalidated(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    approval = create_approval(user_id="test_buyer", quote_id=quote.quote_id, db=db_session)
    consume_approval(db=db_session, approval_id=approval.approval_id)

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.CONSUMED

    client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 55000},
    )

    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.CONSUMED


# 12. Requote exceeding budget requires requirement change
def test_requote_exceeding_budget_requires_requirement_change(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session, price_limit=50000)

    # Spike price to $550.00 (exceeds $500.00 budget)
    client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 55000},
    )

    res = client.post(f"/api/quotes/{quote.quote_id}/requote", headers=USER_HEADERS)
    assert res.status_code == 400
    err = res.json()["error"]
    assert err["code"] == "PRODUCT_NOT_ELIGIBLE"
    assert err["details"]["requires_requirement_change"] is True


# 13. Update intent requirements creates chained intent
def test_update_intent_requirements_creates_chained_intent(client, db_session):
    intent, _ = _setup_intent_and_quote(client, db_session, price_limit=50000)

    res = client.post(
        f"/api/intents/{intent.intent_id}/requirements",
        headers=USER_HEADERS,
        json={"max_total_minor": 60000},
    )
    assert res.status_code == 201
    data = res.json()
    assert data["previous_intent_id"] == intent.intent_id
    assert data["requirements"]["max_total_minor"] == 60000


# 14. Requote within budget succeeds
def test_requote_within_budget_succeeds(client, db_session):
    intent, quote = _setup_intent_and_quote(client, db_session, price_limit=50000)

    # Spike price to $490.00 (under $500.00 budget)
    client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 49000},
    )

    res = client.post(f"/api/quotes/{quote.quote_id}/requote", headers=USER_HEADERS)
    assert res.status_code == 201
    data = res.json()
    assert "new_quote" in data
    assert data["new_quote"]["total_minor"] == 49000
    assert data["comparison"]["old_total_minor"] == 48000
    assert data["comparison"]["new_total_minor"] == 49000
    assert len(data["comparison"]["changes"]) >= 1


# 15. Reword change explanation preserves numbers exactly; fallback on alteration
def test_reword_change_explanation_numeric_validation():
    changes = [{"field": "price_minor", "approved": 48000, "current": 54000}]
    base_message = "The approved price was $480.00. The current price is $540.00."

    # Valid candidate: preserves numbers
    valid_candidate = "Notice: The price changed from $480.00 to $540.00."
    msg, src = validate_and_fallback_reword(valid_candidate, changes, base_message)
    assert msg == valid_candidate
    assert src == "ai"

    # Invalid candidate: introduces a new hallucinated number ($999)
    hallucinated_candidate = "Price jumped to $999.00 from $480.00."
    msg, src = validate_and_fallback_reword(hallucinated_candidate, changes, base_message)
    assert msg == base_message
    assert src == "template"


# 16. Timeline strict chronological ordering
def test_timeline_strict_chronological_ordering(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    create_approval(user_id="test_buyer", quote_id=quote.quote_id, db=db_session)

    res = client.get(f"/api/quotes/{quote.quote_id}/events", headers=USER_HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert data["total_events"] >= 2
    events = data["events"]
    # Verify chronological created_at
    for i in range(len(events) - 1):
        t1 = datetime.fromisoformat(events[i]["created_at"])
        t2 = datetime.fromisoformat(events[i + 1]["created_at"])
        assert t1 <= t2


# 17. Timeline redacts sensitive tokens and secrets
def test_timeline_redacts_sensitive_keys(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)
    record_event(
        db=db_session,
        event_type=AuditEventType.PRE_CREATE_VALIDATION_PASSED,
        quote_id=quote.quote_id,
        payload={
            "api_key": "super_secret_key_123",
            "paypal_token": "bearer_secret_xyz",
            "public_info": "safe_data",
        },
    )
    db_session.commit()

    res = client.get(f"/api/quotes/{quote.quote_id}/events", headers=USER_HEADERS)
    assert res.status_code == 200
    data = res.json()
    sensitive_event = [e for e in data["events"] if e["payload"].get("public_info") == "safe_data"][0]
    assert sensitive_event["payload"]["api_key"] == "[REDACTED]"
    assert sensitive_event["payload"]["paypal_token"] == "[REDACTED]"
    assert sensitive_event["payload"]["public_info"] == "safe_data"


# 18. Timeline returns 404 for quote belonging to another user
def test_timeline_ownership_enforcement(client, db_session):
    _, quote = _setup_intent_and_quote(client, db_session)

    # Another user queries the quote
    res = client.get(f"/api/quotes/{quote.quote_id}/events", headers=OTHER_USER_HEADERS)
    assert res.status_code == 404


# 19. Demo reset restores catalog to v1 initial state
def test_demo_reset_restores_catalog(client, db_session):
    # Mutate laptop_b to v2
    client.patch(
        "/api/demo/products/laptop_b",
        headers=ADMIN_HEADERS,
        json={"price_minor": 60000},
    )

    # Reset
    res = client.post("/api/demo/reset", headers=ADMIN_HEADERS)
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ok"
    assert data["reset_products"] == 5

    # Check that laptop_b current version is v1 at 48000
    curr_pv = db_session.execute(
        select(ProductVersion).where(ProductVersion.product_id == "laptop_b", ProductVersion.is_current.is_(True))
    ).scalar_one()
    assert curr_pv.version == 1
    assert curr_pv.price_minor == 48000
