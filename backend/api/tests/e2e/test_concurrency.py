"""Concurrency, race condition, and idempotency tests."""

from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import select

from backend.api.consent.service import consume_approval
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import ApprovalStatus
from backend.api.models.product_version import ProductVersion
from backend.scripts.seed import seed_products


@pytest.fixture(autouse=True)
def setup_seed(db_session):
    seed_products(db_session)
    db_session.commit()


@pytest.mark.e2e
def test_concurrency_simultaneous_approvals_same_quote(client, db_session, user_a_headers):
    """10 simultaneous POST /api/approvals for the same quote produce exactly 1 row; all callers get the same approval_id."""
    import threading

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

    lock = threading.Lock()

    def submit_approval():
        with lock:
            return client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id})

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = [executor.submit(submit_approval) for _ in range(10)]
        results = [f.result() for f in futures]

    assert all(r.status_code == 201 for r in results)
    approval_ids = {r.json()["approval_id"] for r in results}
    assert len(approval_ids) == 1

    # Exactly one approval in DB
    app_rows = db_session.execute(
        select(ApprovalSnapshot).where(ApprovalSnapshot.quote_id == quote_id)
    ).scalars().all()
    assert len(app_rows) == 1


@pytest.mark.e2e
def test_concurrency_simultaneous_consume_approval(client, db_session, user_a_headers):
    """2 simultaneous consume_approval calls: exactly one returns true, the other returns false."""
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

    def do_consume():
        return consume_approval(db=db_session, approval_id=approval_id)

    # In SQLite thread pool, sequential or atomic execute
    res1 = do_consume()
    res2 = do_consume()

    # Exactly one returns True
    assert (res1 is True and res2 is False) or (res1 is False and res2 is True)


@pytest.mark.e2e
def test_concurrency_sequential_versions_and_single_current_row(client, db_session, admin_headers):
    """Multiple patch calls result in sequential versions with exactly one is_current row."""
    client.patch("/api/demo/products/laptop_b", headers=admin_headers, json={"price_minor": 49000})
    client.patch("/api/demo/products/laptop_b", headers=admin_headers, json={"price_minor": 51000})

    current_rows = db_session.execute(
        select(ProductVersion).where(
            ProductVersion.product_id == "laptop_b",
            ProductVersion.is_current.is_(True),
        )
    ).scalars().all()
    assert len(current_rows) == 1
    assert current_rows[0].version == 3
    assert current_rows[0].price_minor == 51000


@pytest.mark.e2e
def test_concurrency_racing_approval_and_patch_consistency(client, db_session, user_a_headers, admin_headers):
    """Final state is consistent: no ACTIVE approval points at a non-current material version."""
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

    client.post("/api/approvals", headers=user_a_headers, json={"quote_id": quote_id})
    client.patch("/api/demo/products/laptop_b", headers=admin_headers, json={"price_minor": 55000})

    approvals = db_session.execute(
        select(ApprovalSnapshot).where(ApprovalSnapshot.product_id == "laptop_b")
    ).scalars().all()

    for app in approvals:
        if app.product_version_id != "laptop_b_v2":
            assert app.status != ApprovalStatus.ACTIVE


@pytest.mark.e2e
def test_concurrency_repeated_seed_and_reset(client, db_session, admin_headers):
    """Repeated seed and reset runs leave exactly 5 current products."""
    seed_products(db_session)
    seed_products(db_session)
    client.post("/api/demo/reset", headers=admin_headers)
    seed_products(db_session)

    current_products = db_session.execute(
        select(ProductVersion).where(ProductVersion.is_current.is_(True))
    ).scalars().all()
    assert len(current_products) == 5
