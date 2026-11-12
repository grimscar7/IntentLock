"""Payment tests: PayPal is replaced by a fake, Vishnu's real validators are used."""
import httpx
import pytest
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import ApprovalStatus
from backend.api.payments import paypal_client as pp
from backend.scripts.seed import seed_products
from sqlalchemy import select

U1 = {"X-User-Id": "buyer_1"}
U2 = {"X-User-Id": "buyer_2"}


@pytest.fixture(autouse=True)
def setup_seed(db_session):
    seed_products(db_session)
    db_session.commit()


class FakePayPal:
    def __init__(self):
        self.last = {}
        self.captures = 0
        self.creates = 0
        self.order_status = "APPROVED"
        self.amount_override = None
        self.capture_exc = None

    def create_order(self, amount_minor, currency, reference_id, description, request_id, custom_id=None):
        self.creates += 1
        self.last = dict(amount_minor=amount_minor, currency=currency, reference_id=reference_id,
                         custom_id=custom_id)
        return {"id": "ORDER-1"}

    def get_order(self, order_id):
        unit = {
            "reference_id": self.last["reference_id"],
            "custom_id": self.last["custom_id"],
            "amount": {"currency_code": self.last["currency"],
                       "value": self.amount_override or pp.minor_to_str(self.last["amount_minor"])},
        }
        if self.order_status == "COMPLETED":
            unit["payments"] = {"captures": [{"id": "CAP-1", "status": "COMPLETED"}]}
        return {"id": order_id, "status": self.order_status, "purchase_units": [unit]}

    def capture_order(self, order_id, request_id):
        if self.capture_exc:
            raise self.capture_exc
        self.captures += 1
        return {"status": "COMPLETED",
                "purchase_units": [{"payments": {"captures": [{"id": "CAP-1", "status": "COMPLETED"}]}}]}


@pytest.fixture
def paypal(monkeypatch):
    fake = FakePayPal()
    monkeypatch.setattr(pp, "create_order", fake.create_order)
    monkeypatch.setattr(pp, "get_order", fake.get_order)
    monkeypatch.setattr(pp, "capture_order", fake.capture_order)
    return fake


def make_approval(client, headers=U1):
    intent = client.post("/api/intents", headers=headers, json={
        "raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"}).json()["intent_id"]
    quote = client.post("/api/quotes", headers=headers, json={
        "intent_id": intent, "product_id": "laptop_b", "quantity": 1}).json()["quote_id"]
    return client.post("/api/approvals", headers=headers, json={"quote_id": quote}).json()["approval_id"]


def create(client, approval_id, headers=U1):
    return client.post("/api/payments/create", headers=headers, json={"approval_id": approval_id})


def capture(client, attempt_id, headers=U1):
    return client.post(f"/api/payments/{attempt_id}/capture", headers=headers)


def test_happy_path_pays_and_consumes_approval(client, db_session, paypal):
    approval_id = make_approval(client)
    attempt = create(client, approval_id).json()["attempt_id"]
    r = capture(client, attempt)
    assert r.status_code == 200
    assert r.json()["state"] == "PAID"
    assert r.json()["capture_id"] == "CAP-1"
    appr = db_session.execute(select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)).scalar_one()
    db_session.refresh(appr)
    assert appr.status == ApprovalStatus.CONSUMED


def test_order_uses_server_owned_values(client, paypal):
    create(client, make_approval(client))
    assert paypal.last["amount_minor"] == 48000
    assert paypal.last["currency"] == "USD"
    assert paypal.last["reference_id"] == "demo_store"


def test_double_click_capture_takes_money_once(client, paypal):
    attempt = create(client, make_approval(client)).json()["attempt_id"]
    capture(client, attempt)
    r2 = capture(client, attempt)
    assert r2.json()["state"] == "PAID"
    assert paypal.captures == 1


def test_double_click_create_reuses_order(client, paypal):
    approval_id = make_approval(client)
    a1 = create(client, approval_id).json()
    a2 = create(client, approval_id).json()
    assert a1["attempt_id"] == a2["attempt_id"]
    assert paypal.creates == 1


def test_price_spike_blocks_create(client, paypal, admin_headers):
    approval_id = make_approval(client)
    client.patch("/api/demo/products/laptop_b", headers=admin_headers, json={"price_minor": 54000})
    r = create(client, approval_id)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "APPROVAL_INVALIDATED"
    assert paypal.creates == 0


def test_price_spike_between_create_and_capture_blocks_capture(client, paypal, admin_headers):
    approval_id = make_approval(client)
    attempt = create(client, approval_id).json()["attempt_id"]
    client.patch("/api/demo/products/laptop_b", headers=admin_headers, json={"price_minor": 55000})
    r = capture(client, attempt)
    assert r.status_code == 409
    assert paypal.captures == 0


def test_other_users_attempt_is_404(client, paypal):
    attempt = create(client, make_approval(client)).json()["attempt_id"]
    assert capture(client, attempt, headers=U2).status_code == 404
    assert client.get(f"/api/payments/{attempt}", headers=U2).status_code == 404


def test_other_users_approval_is_rejected(client, paypal):
    approval_id = make_approval(client)
    r = create(client, approval_id, headers=U2)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
    assert paypal.creates == 0


def test_missing_login_is_401(client):
    assert client.post("/api/payments/create", json={"approval_id": "x"}).status_code == 401


def test_amount_mismatch_is_rejected(client, paypal):
    attempt = create(client, make_approval(client)).json()["attempt_id"]
    paypal.amount_override = "1.00"
    r = capture(client, attempt)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "ORDER_MISMATCH"
    assert paypal.captures == 0


def test_capture_before_buyer_approval_is_rejected(client, paypal):
    attempt = create(client, make_approval(client)).json()["attempt_id"]
    paypal.order_status = "CREATED"
    r = capture(client, attempt)
    assert r.json()["error"]["code"] == "ORDER_NOT_APPROVED"
    assert paypal.captures == 0
    assert client.get(f"/api/payments/{attempt}", headers=U1).json()["state"] == "PAYPAL_ORDER_CREATED"


def test_timeout_becomes_unknown_blocks_repay_then_reconciles(client, paypal):
    approval_id = make_approval(client)
    attempt = create(client, approval_id).json()["attempt_id"]
    paypal.capture_exc = httpx.ReadTimeout("slow")
    r = capture(client, attempt)
    assert r.status_code == 504
    assert client.get(f"/api/payments/{attempt}", headers=U1).json()["state"] == "PAYMENT_STATUS_UNKNOWN"
    assert capture(client, attempt).json()["error"]["code"] == "PAYMENT_STATUS_UNKNOWN"
    assert create(client, approval_id).json()["error"]["code"] == "PAYMENT_STATUS_UNKNOWN"  # no second payment
    paypal.order_status = "COMPLETED"
    r = client.post(f"/api/payments/{attempt}/reconcile", headers=U1)
    assert r.json()["state"] == "PAID"


def test_declined_card_can_be_retried(client, paypal):
    attempt = create(client, make_approval(client)).json()["attempt_id"]
    paypal.capture_exc = pp.PayPalError(422, {"details": [{"issue": "INSTRUMENT_DECLINED"}]})
    r = capture(client, attempt)
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "INSTRUMENT_DECLINED"
    assert r.json()["error"]["details"]["retryable"] is True
    assert client.get(f"/api/payments/{attempt}", headers=U1).json()["state"] == "PAYPAL_ORDER_CREATED"
