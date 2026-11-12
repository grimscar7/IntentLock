"""The ONLY file that talks to PayPal: token, create, read and capture orders.

Secrets come from environment variables (backend/.env locally, Render settings in production).
"""
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Local dev: read backend/.env. Existing environment variables (Render) are NOT overridden.
load_dotenv(Path(__file__).resolve().parents[2] / ".env")


class PayPalError(Exception):
    """PayPal replied with an error. Keeps status and body so callers can react."""

    def __init__(self, status_code: int, body):
        self.status_code = status_code
        self.body = body
        super().__init__(f"PayPal error {status_code}")

    @property
    def issue(self) -> str | None:
        """Machine-readable reason, e.g. 'INSTRUMENT_DECLINED'."""
        if isinstance(self.body, dict):
            details = self.body.get("details")
            if isinstance(details, list) and len(details) > 0 and isinstance(details[0], dict):
                return details[0].get("issue")
            if "name" in self.body and isinstance(self.body["name"], str):
                return self.body["name"]
        return None


_token = {"value": None, "expires_at": 0.0}


def _base() -> str:
    from backend.api.config import get_settings
    return os.getenv("PAYPAL_BASE_URL") or get_settings().PAYPAL_BASE_URL or "https://api-m.sandbox.paypal.com"


def _credentials() -> tuple[str, str]:
    from backend.api.config import get_settings
    s = get_settings()
    client_id = os.getenv("PAYPAL_CLIENT_ID") or s.PAYPAL_CLIENT_ID or ""
    client_secret = os.getenv("PAYPAL_CLIENT_SECRET") or s.PAYPAL_CLIENT_SECRET or ""
    return client_id.strip(), client_secret.strip()


def _safe_json(r: httpx.Response):
    try:
        return r.json()
    except Exception:
        return {"raw": r.text[:200]}


def minor_to_str(amount_minor: int) -> str:
    """48000 -> '480.00'. PayPal wants a string with 2 decimals; we never use floats."""
    if amount_minor < 0:
        raise ValueError(f"amount_minor must be non-negative, got {amount_minor}")
    return f"{amount_minor // 100}.{amount_minor % 100:02d}"


def _get_token() -> str:
    if _token["value"] and time.time() < _token["expires_at"] - 60:
        return _token["value"]
    client_id, client_secret = _credentials()
    if not client_id or not client_secret:
        raise PayPalError(401, {
            "error": "PAYPAL_CREDENTIALS_MISSING",
            "message": "Missing PayPal credentials. Set PAYPAL_CLIENT_ID and PAYPAL_CLIENT_SECRET in .env",
        })
    r = httpx.post(
        f"{_base()}/v1/oauth2/token",
        auth=(client_id, client_secret),
        data={"grant_type": "client_credentials"},
        headers={"Accept": "application/json"},
        timeout=15,
    )
    if r.status_code != 200:
        raise PayPalError(r.status_code, _safe_json(r))
    data = r.json()
    _token["value"] = data["access_token"]
    _token["expires_at"] = time.time() + data["expires_in"]
    return _token["value"]


def _headers(request_id: str | None = None) -> dict:
    h = {"Authorization": f"Bearer {_get_token()}", "Content-Type": "application/json"}
    if request_id:
        # Idempotency key: the same key twice gives the same result, never a double charge.
        h["PayPal-Request-Id"] = request_id
    return h


def create_order(amount_minor: int, currency: str, reference_id: str, description: str,
                 request_id: str, custom_id: str | None = None) -> dict:
    unit = {
        "reference_id": reference_id,  # we use the merchant id, and re-check it before capture
        "description": description[:120],
        "amount": {"currency_code": currency, "value": minor_to_str(amount_minor)},
    }
    if custom_id:
        unit["custom_id"] = custom_id  # binds this PayPal order to ONE payment attempt
    r = httpx.post(f"{_base()}/v2/checkout/orders",
                   json={"intent": "CAPTURE", "purchase_units": [unit]},
                   headers=_headers(request_id), timeout=20)
    if r.status_code not in (200, 201):
        raise PayPalError(r.status_code, _safe_json(r))
    return r.json()


def get_order(order_id: str) -> dict:
    r = httpx.get(f"{_base()}/v2/checkout/orders/{order_id}", headers=_headers(), timeout=20)
    if r.status_code != 200:
        raise PayPalError(r.status_code, _safe_json(r))
    return r.json()


def capture_order(order_id: str, request_id: str) -> dict:
    r = httpx.post(f"{_base()}/v2/checkout/orders/{order_id}/capture",
                   headers=_headers(request_id), timeout=30)
    if r.status_code not in (200, 201):
        raise PayPalError(r.status_code, _safe_json(r))
    return r.json()
