"""Runs one buyer flow against the LOCAL backend and prints each raw response."""
import json
import sys
import httpx

c = httpx.Client(base_url="http://127.0.0.1:8000", headers={"X-User-Id": "buyer_1"}, timeout=30)


def show(title, r):
    print(f"\n===== {title} -> HTTP {r.status_code} =====")
    try:
        print(json.dumps(r.json(), indent=2))
    except Exception:
        print(r.text[:500])
    return r


show("health", c.get("/health"))
show("catalog products", c.get("/api/catalog/products"))
r = show("create intent", c.post("/api/intents", json={
    "raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"}))
if r.status_code != 201:
    sys.exit("intent failed")
intent_id = r.json()["intent_id"]
show("options", c.get(f"/api/intents/{intent_id}/options"))
r = show("create quote", c.post("/api/quotes", json={
    "intent_id": intent_id, "product_id": "laptop_b", "quantity": 1}))
if r.status_code != 201:
    sys.exit("quote failed")
quote_id = r.json()["quote_id"]
show("get quote", c.get(f"/api/quotes/{quote_id}"))
r = show("approve", c.post("/api/approvals", json={"quote_id": quote_id}))
if r.status_code != 201:
    sys.exit("approval failed")
approval_id = r.json()["approval_id"]
show("validate", c.post(f"/api/approvals/{approval_id}/validate"))
show("events", c.get(f"/api/quotes/{quote_id}/events"))
