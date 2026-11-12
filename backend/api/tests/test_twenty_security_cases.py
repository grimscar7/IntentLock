"""Twenty comprehensive security, vulnerability, PayPal sandbox, and intent-locking test cases.

Author: Antigravity AI (Vishnu)
Suite: IntentLock Full Security & Integration Verification
"""

import os
import uuid

import httpx
import jwt
import pytest
from backend.api.config import get_settings
from backend.api.payments import paypal_client as pp
from backend.api.payments.paypal_client import PayPalError, minor_to_str
from backend.scripts.seed import seed_products

# Test users
USER_A = {"X-User-Id": "user_alpha"}
USER_B = {"X-User-Id": "user_beta"}


@pytest.fixture(autouse=True)
def setup_seed_data(db_session):
    seed_products(db_session)
    db_session.commit()


class MockPayPalService:
    """Mock PayPal server simulating PayPal Sandbox responses."""

    def __init__(self):
        self.creates = 0
        self.captures = 0
        self.last_order = {}
        self.order_status = "APPROVED"
        self.amount_override = None
        self.currency_override = None
        self.custom_id_override = None
        self.capture_exception = None

    def create_order(self, amount_minor, currency, reference_id, description, request_id, custom_id=None):
        self.creates += 1
        self.last_order = {
            "amount_minor": amount_minor,
            "currency": currency,
            "reference_id": reference_id,
            "custom_id": custom_id,
            "id": f"ORDER-{uuid.uuid4().hex[:8].upper()}",
        }
        return {"id": self.last_order["id"]}

    def get_order(self, order_id):
        unit = {
            "reference_id": self.last_order.get("reference_id", "demo_store"),
            "custom_id": self.custom_id_override or self.last_order.get("custom_id"),
            "amount": {
                "currency_code": self.currency_override or self.last_order.get("currency", "USD"),
                "value": self.amount_override or minor_to_str(self.last_order.get("amount_minor", 48000)),
            },
        }
        if self.order_status == "COMPLETED":
            unit["payments"] = {"captures": [{"id": "CAP-SIMULATED-100", "status": "COMPLETED"}]}
        return {
            "id": order_id,
            "status": self.order_status,
            "purchase_units": [unit],
        }

    def capture_order(self, order_id, request_id):
        if self.capture_exception:
            raise self.capture_exception
        self.captures += 1
        return {
            "status": "COMPLETED",
            "purchase_units": [{
                "payments": {"captures": [{"id": f"CAP-{uuid.uuid4().hex[:8].upper()}", "status": "COMPLETED"}]}
            }],
        }


@pytest.fixture
def mock_paypal(monkeypatch):
    mock = MockPayPalService()
    monkeypatch.setattr(pp, "create_order", mock.create_order)
    monkeypatch.setattr(pp, "get_order", mock.get_order)
    monkeypatch.setattr(pp, "capture_order", mock.capture_order)
    return mock


def helper_create_active_approval(client, headers=USER_A):
    """Helper creating a valid quote and approval for testing."""
    r_intent = client.post(
        "/api/intents",
        headers=headers,
        json={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    assert r_intent.status_code == 201, f"Intent creation failed: {r_intent.text}"
    intent_id = r_intent.json()["intent_id"]

    r_quote = client.post(
        "/api/quotes",
        headers=headers,
        json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    )
    assert r_quote.status_code == 201, f"Quote creation failed: {r_quote.text}"
    quote_id = r_quote.json()["quote_id"]

    r_approval = client.post(
        "/api/approvals",
        headers=headers,
        json={"quote_id": quote_id},
    )
    assert r_approval.status_code == 201, f"Approval failed: {r_approval.text}"
    return r_approval.json()["approval_id"]


# ==============================================================================
# TEST CASE 1: Live PayPal Sandbox OAuth2 Token Authentication
# ==============================================================================
def test_01_sandbox_live_oauth_token_generation():
    """Verify live OAuth2 token exchange with PayPal Sandbox API using sandbox credentials."""
    base_url = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
    client_id = os.getenv("PAYPAL_CLIENT_ID", "")
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET", "")

    assert client_id, "PAYPAL_CLIENT_ID must be set"
    assert client_secret, "PAYPAL_CLIENT_SECRET must be set"

    r = httpx.post(
        f"{base_url}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=15.0,
    )
    assert r.status_code == 200, f"PayPal OAuth rejected credentials: {r.text}"
    data = r.json()
    assert "access_token" in data
    assert data.get("token_type") == "Bearer"
    assert data.get("expires_in") > 0


# ==============================================================================
# TEST CASE 2: PayPal Client Fails Closed on Missing Credentials
# ==============================================================================
def test_02_paypal_client_missing_credentials_fails_closed(monkeypatch):
    """Verify paypal_client raises 401 PAYPAL_CREDENTIALS_MISSING when credentials are absent."""
    monkeypatch.setattr(pp, "_credentials", lambda: ("", ""))
    monkeypatch.setattr(pp, "_token", {"value": None, "expires_at": 0.0})

    with pytest.raises(PayPalError) as exc_info:
        pp._get_token()

    assert exc_info.value.status_code == 401
    assert exc_info.value.body.get("error") == "PAYPAL_CREDENTIALS_MISSING"


# ==============================================================================
# TEST CASE 3: Auth Security - Empty or Whitespace X-User-Id Header Rejected
# ==============================================================================
def test_03_auth_reject_empty_or_whitespace_x_user_id(client):
    """Verify whitespace or empty X-User-Id header cannot bypass authentication."""
    r_empty = client.post("/api/intents", headers={"X-User-Id": ""}, json={"raw_text": "laptop"})
    assert r_empty.status_code == 401

    r_whitespace = client.post("/api/intents", headers={"X-User-Id": "   "}, json={"raw_text": "laptop"})
    assert r_whitespace.status_code == 401


# ==============================================================================
# TEST CASE 4: Auth Security - Production Mode Rejects X-User-Id Header
# ==============================================================================
def test_04_auth_prod_mode_strictly_rejects_x_user_id(client, monkeypatch):
    """Verify that in production mode, X-User-Id dev header is strictly rejected."""
    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "production")

    r = client.post("/api/intents", headers={"X-User-Id": "attacker"}, json={"raw_text": "laptop"})
    assert r.status_code == 401
    assert "Provide a valid Bearer token" in r.json()["error"]["message"]


# ==============================================================================
# TEST CASE 5: Auth Security - Forged JWT Signature Rejected
# ==============================================================================
def test_05_auth_jwt_invalid_signature_rejected(client):
    """Verify forged JWT signed with wrong secret key is rejected with 401."""
    tampered_token = jwt.encode(
        {"sub": "victim_user"},
        "attacker_secret_key_that_is_at_least_32_bytes_long_12345",
        algorithm="HS256",
    )
    r = client.post(
        "/api/intents",
        headers={"Authorization": f"Bearer {tampered_token}"},
        json={"raw_text": "laptop"},
    )
    assert r.status_code == 401
    assert "Invalid or expired authentication token" in r.json()["error"]["message"]


# ==============================================================================
# TEST CASE 6: Auth Security - JWT Missing 'sub' Claim Rejected
# ==============================================================================
def test_06_auth_jwt_missing_sub_claim_rejected(client):
    """Verify validly signed JWT missing 'sub' claim is rejected with 401."""
    secret = get_settings().SUPABASE_JWT_SECRET
    bad_token = jwt.encode({"role": "authenticated"}, secret, algorithm="HS256")
    r = client.post(
        "/api/intents",
        headers={"Authorization": f"Bearer {bad_token}"},
        json={"raw_text": "laptop"},
    )
    assert r.status_code == 401
    assert "missing user identifier" in r.json()["error"]["message"]


# ==============================================================================
# TEST CASE 7: Demo Admin Route Security - Invalid Token Rejected (403)
# ==============================================================================
def test_07_admin_route_forbidden_with_invalid_token(client):
    """Verify demo catalog endpoints reject invalid X-Demo-Admin-Token with 403."""
    r = client.post("/api/demo/reset", headers={"X-Demo-Admin-Token": "wrong_secret_token"})
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"


# ==============================================================================
# TEST CASE 8: Demo Admin Route Security - Production Mode Returns 404
# ==============================================================================
def test_08_admin_route_in_production_returns_404(client, monkeypatch):
    """Verify demo admin management endpoints return 404 in production to prevent exposure."""
    settings = get_settings()
    monkeypatch.setattr(settings, "ENV", "production")

    r = client.post("/api/demo/reset", headers={"X-Demo-Admin-Token": settings.DEMO_ADMIN_TOKEN})
    assert r.status_code == 404


# ==============================================================================
# TEST CASE 9: Payment Invariant - Negative Amount in minor_to_str Fails Closed
# ==============================================================================
def test_09_minor_to_str_negative_amount_raises_valueerror():
    """Verify negative amount passed to minor_to_str raises ValueError."""
    with pytest.raises(ValueError, match="amount_minor must be non-negative"):
        minor_to_str(-500)


# ==============================================================================
# TEST CASE 10: Robustness - PayPalError.issue Handles Non-Standard Body Formats
# ==============================================================================
def test_10_paypal_error_safe_issue_parsing():
    """Verify PayPalError.issue property safely handles varied payload formats without raising errors."""
    err_list = PayPalError(400, {"details": [{"issue": "ORDER_ALREADY_CAPTURED"}]})
    assert err_list.issue == "ORDER_ALREADY_CAPTURED"

    err_empty_details = PayPalError(400, {"details": []})
    assert err_empty_details.issue is None

    err_named = PayPalError(422, {"name": "UNPROCESSABLE_ENTITY"})
    assert err_named.issue == "UNPROCESSABLE_ENTITY"

    err_string_body = PayPalError(500, "Internal Server Error")
    assert err_string_body.issue is None


# ==============================================================================
# TEST CASE 11: IntentLock - Merchant Price Spike Blocks Payment Creation
# ==============================================================================
def test_11_intent_lock_blocks_merchant_price_spike_before_create(client, admin_headers, mock_paypal):
    """Verify price spike after approval blocks /api/payments/create with 409 APPROVAL_INVALIDATED."""
    approval_id = helper_create_active_approval(client)

    # Merchant spikes price from 48000 to 55000
    r_spike = client.patch(
        "/api/demo/products/laptop_b",
        headers=admin_headers,
        json={"price_minor": 55000},
    )
    assert r_spike.status_code == 200

    r_create = client.post(
        "/api/payments/create",
        headers=USER_A,
        json={"approval_id": approval_id},
    )
    assert r_create.status_code == 409
    assert r_create.json()["error"]["code"] == "APPROVAL_INVALIDATED"
    assert mock_paypal.creates == 0


# ==============================================================================
# TEST CASE 12: IntentLock - Merchant Price Spike Blocks Payment Capture
# ==============================================================================
def test_12_intent_lock_blocks_merchant_price_spike_between_create_and_capture(client, admin_headers, mock_paypal):
    """Verify price spike after PayPal order creation blocks capture, making 0 charges."""
    approval_id = helper_create_active_approval(client)

    r_create = client.post(
        "/api/payments/create",
        headers=USER_A,
        json={"approval_id": approval_id},
    )
    assert r_create.status_code == 200
    attempt_id = r_create.json()["attempt_id"]
    assert mock_paypal.creates == 1

    # Merchant spikes price before buyer captures
    client.patch("/api/demo/products/laptop_b", headers=admin_headers, json={"price_minor": 58000})

    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    assert r_cap.status_code == 409
    assert mock_paypal.captures == 0


# ==============================================================================
# TEST CASE 13: IntentLock - Tampered Order Amount Mismatch Detected & Blocked
# ==============================================================================
def test_13_intent_lock_tampered_order_amount_mismatch(client, mock_paypal):
    """Verify order amount mismatch returned from PayPal causes immediate capture rejection."""
    approval_id = helper_create_active_approval(client)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    mock_paypal.amount_override = "999.00"  # Tampered PayPal amount
    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    assert r_cap.status_code == 409
    assert r_cap.json()["error"]["code"] == "ORDER_MISMATCH"
    assert mock_paypal.captures == 0


# ==============================================================================
# TEST CASE 14: IntentLock - Tampered Currency Mismatch Detected & Blocked
# ==============================================================================
def test_14_intent_lock_tampered_currency_mismatch(client, mock_paypal):
    """Verify order currency mismatch (e.g., USD switched to EUR) is blocked with ORDER_MISMATCH."""
    approval_id = helper_create_active_approval(client)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    mock_paypal.currency_override = "EUR"  # Tampered currency
    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    assert r_cap.status_code == 409
    assert r_cap.json()["error"]["code"] == "ORDER_MISMATCH"
    assert mock_paypal.captures == 0


# ==============================================================================
# TEST CASE 15: IntentLock - Tampered Custom ID Mismatch Blocked
# ==============================================================================
def test_15_intent_lock_tampered_custom_id_mismatch(client, mock_paypal):
    """Verify tampered custom_id in PayPal order prevents unauthorized attempt binding."""
    approval_id = helper_create_active_approval(client)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    mock_paypal.custom_id_override = "rogue-attempt-id-xyz"
    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    assert r_cap.status_code == 409
    assert r_cap.json()["error"]["code"] == "ORDER_MISMATCH"
    assert mock_paypal.captures == 0


# ==============================================================================
# TEST CASE 16: Security - Cross-User Isolation on Payment Creation
# ==============================================================================
def test_16_intent_lock_cross_user_isolation_create_blocked(client, mock_paypal):
    """Verify User B cannot create a payment attempt using User A's approval snapshot."""
    approval_id = helper_create_active_approval(client, headers=USER_A)

    r_b = client.post("/api/payments/create", headers=USER_B, json={"approval_id": approval_id})
    assert r_b.status_code == 409
    assert r_b.json()["error"]["code"] == "APPROVAL_NOT_FOUND"
    assert mock_paypal.creates == 0


# ==============================================================================
# TEST CASE 17: Security - Cross-User Isolation on Payment Capture & Status
# ==============================================================================
def test_17_intent_lock_cross_user_isolation_capture_blocked(client, mock_paypal):
    """Verify User B cannot capture or view status of User A's payment attempt (returns 404)."""
    approval_id = helper_create_active_approval(client, headers=USER_A)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_B)
    assert r_cap.status_code == 404

    r_status = client.get(f"/api/payments/{attempt_id}", headers=USER_B)
    assert r_status.status_code == 404


# ==============================================================================
# TEST CASE 18: Security - Replay Attack Prevented on Consumed Approval
# ==============================================================================
def test_18_approval_replay_attack_prevented(client, mock_paypal):
    """Verify once an approval is captured and consumed, creating another payment is rejected."""
    approval_id = helper_create_active_approval(client)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    # First capture succeeds
    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    assert r_cap.status_code == 200
    assert r_cap.json()["state"] == "PAID"

    # Attempt to replay the consumed approval for a new payment
    r_replay = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    assert r_replay.status_code == 409
    assert r_replay.json()["error"]["code"] in ("ALREADY_PAID", "APPROVAL_ALREADY_CONSUMED")


# ==============================================================================
# TEST CASE 19: Concurrency - Idempotent Double Capture Execution
# ==============================================================================
def test_19_concurrent_double_capture_idempotency(client, mock_paypal):
    """Verify duplicate capture calls succeed idempotently and invoke PayPal capture only once."""
    approval_id = helper_create_active_approval(client)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    r_cap1 = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    r_cap2 = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)

    assert r_cap1.status_code == 200
    assert r_cap2.status_code == 200
    assert r_cap1.json()["state"] == "PAID"
    assert r_cap2.json()["state"] == "PAID"
    assert mock_paypal.captures == 1


# ==============================================================================
# TEST CASE 20: Resilience - Network Timeout Enters UNKNOWN and Reconciles Cleanly
# ==============================================================================
def test_20_network_timeout_marks_unknown_and_reconciles_cleanly(client, mock_paypal):
    """Verify capture timeout marks state PAYMENT_STATUS_UNKNOWN (504), locks replay, and reconciles to PAID."""
    approval_id = helper_create_active_approval(client)
    r_create = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    attempt_id = r_create.json()["attempt_id"]

    # Simulate network timeout during PayPal capture
    mock_paypal.capture_exception = httpx.ReadTimeout("Simulated PayPal gateway timeout")
    r_cap = client.post(f"/api/payments/{attempt_id}/capture", headers=USER_A)
    assert r_cap.status_code == 504
    assert r_cap.json()["error"]["code"] == "PAYMENT_STATUS_UNKNOWN"

    # Verify attempt is locked in PAYMENT_STATUS_UNKNOWN
    r_status = client.get(f"/api/payments/{attempt_id}", headers=USER_A)
    assert r_status.json()["state"] == "PAYMENT_STATUS_UNKNOWN"

    # Verify second payment attempt on this approval is blocked
    r_repay = client.post("/api/payments/create", headers=USER_A, json={"approval_id": approval_id})
    assert r_repay.status_code == 409
    assert r_repay.json()["error"]["code"] == "PAYMENT_STATUS_UNKNOWN"

    # Reconcile after PayPal resolves order to COMPLETED
    mock_paypal.order_status = "COMPLETED"
    mock_paypal.capture_exception = None
    r_rec = client.post(f"/api/payments/{attempt_id}/reconcile", headers=USER_A)
    assert r_rec.status_code == 200
    assert r_rec.json()["state"] == "PAID"
