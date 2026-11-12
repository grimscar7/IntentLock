"""Standalone check: token -> create order -> buyer approves -> capture."""
import os
import sys
import uuid
import httpx
from dotenv import load_dotenv

load_dotenv()  # reads the .env file

BASE = os.getenv("PAYPAL_BASE_URL", "https://api-m.sandbox.paypal.com")
CLIENT_ID = os.getenv("PAYPAL_CLIENT_ID", "")
SECRET = os.getenv("PAYPAL_CLIENT_SECRET", "")

if not CLIENT_ID or not SECRET:
    sys.exit("Missing PAYPAL_CLIENT_ID or PAYPAL_CLIENT_SECRET in .env")

# 1. Get an access token (proves the keys are valid)
r = httpx.post(f"{BASE}/v1/oauth2/token", auth=(CLIENT_ID, SECRET),
               data={"grant_type": "client_credentials"}, timeout=20)
if r.status_code != 200:
    sys.exit(f"Token failed ({r.status_code}): {r.text[:300]}")
token = r.json()["access_token"]
print("1. Token OK")

headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 2. Create an order for 10.00 USD
order_body = {
    "intent": "CAPTURE",
    "purchase_units": [{
        "reference_id": "smoke-test",
        "amount": {"currency_code": "USD", "value": "10.00"},
    }],
    "payment_source": {"paypal": {"experience_context": {
        "return_url": "https://example.com/return",
        "cancel_url": "https://example.com/cancel",
        "user_action": "PAY_NOW",
    }}},
}
r = httpx.post(f"{BASE}/v2/checkout/orders", json=order_body,
               headers={**headers, "PayPal-Request-Id": str(uuid.uuid4())}, timeout=20)
if r.status_code not in (200, 201):
    sys.exit(f"Create order failed ({r.status_code}): {r.text[:500]}")
order = r.json()
print("2. Order created:", order["id"])

link = next((l["href"] for l in order["links"] if l["rel"] in ("payer-action", "approve")), None)
print("\n3. Open this link in your browser, log in as the SANDBOX BUYER and approve:")
print(link)
print("   (After approving you'll land on example.com. That's expected.)")
input("\nPress Enter here AFTER you approved in the browser... ")

# 4. Check status, then capture
r = httpx.get(f"{BASE}/v2/checkout/orders/{order['id']}", headers=headers, timeout=20)
print("4. Order status:", r.json().get("status"))

r = httpx.post(f"{BASE}/v2/checkout/orders/{order['id']}/capture",
               headers={**headers, "PayPal-Request-Id": str(uuid.uuid4())}, timeout=30)
print("5. Capture HTTP status:", r.status_code)
data = r.json()
print("   Order status:", data.get("status"))
try:
    print("   Capture ID:", data["purchase_units"][0]["payments"]["captures"][0]["id"])
except Exception:
    print("   Full response:", data)