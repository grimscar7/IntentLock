"""Live smoke test script for IntentLock backend.

Executes end-to-end verification against a live running server.
Works against dev and staging environments; refuses execution against prod.
"""

import json
import os
import sys
import urllib.request
from typing import Any

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from backend.api.config import get_settings

BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/")
ADMIN_TOKEN = os.getenv("DEMO_ADMIN_TOKEN", get_settings().DEMO_ADMIN_TOKEN)
ENV = os.getenv("ENV", "development").lower()

if ENV in ("prod", "production"):
    print("FATAL: smoke_test.py is disabled in production environments.")
    sys.exit(1)


def request(
    method: str,
    path: str,
    headers: dict[str, str] | None = None,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    url = f"{BASE_URL}{path}"
    hdrs = {"Content-Type": "application/json"}
    if headers:
        hdrs.update(headers)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method)

    try:
        with urllib.request.urlopen(req) as resp:
            status = resp.status
            content = json.loads(resp.read().decode("utf-8"))
            return status, content
    except urllib.error.HTTPError as err:
        status = err.code
        try:
            content = json.loads(err.read().decode("utf-8"))
        except Exception:
            content = {"error": "non_json_error"}
        return status, content


def main():
    print(f"=== Starting IntentLock Smoke Test against {BASE_URL} (ENV={ENV}) ===")
    user_headers = {"X-User-Id": "smoke_test_buyer"}
    admin_headers = {"X-Demo-Admin-Token": ADMIN_TOKEN} if ADMIN_TOKEN else {}

    # Step 1: Health check
    st, res = request("GET", "/health")
    if st == 200 and res.get("status") == "ok":
        print("[PASS] 1. /health endpoint reachable and database connected")
    else:
        print(f"[FAIL] 1. /health returned {st}: {res}")
        sys.exit(1)

    # Step 2: Submit Intent
    st, res = request(
        "POST",
        "/api/intents",
        headers=user_headers,
        body={"raw_text": "Refurbished laptop under $500 with 16GB RAM and 30-day returns"},
    )
    if st == 201 and "intent_id" in res:
        intent_id = res["intent_id"]
        print(f"[PASS] 2. Intent created: {intent_id}")
    else:
        print(f"[FAIL] 2. Intent creation returned {st}: {res}")
        sys.exit(1)

    # Step 3: Query Eligible Options
    st, res = request("GET", f"/api/intents/{intent_id}/options", headers=user_headers)
    if st == 200 and len(res.get("eligible_products", [])) == 1:
        product_id = res["eligible_products"][0]["product_id"]
        print(f"[PASS] 3. Options evaluated: 1 eligible product '{product_id}'")
    else:
        print(f"[FAIL] 3. Options evaluation returned {st}: {res}")
        sys.exit(1)

    # Step 4: Create Quote
    st, res = request(
        "POST",
        "/api/quotes",
        headers=user_headers,
        body={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
    )
    if st == 201 and "quote_id" in res:
        quote_id = res["quote_id"]
        total_minor = res["total_minor"]
        print(f"[PASS] 4. Quote created: {quote_id} (total_minor={total_minor})")
    else:
        print(f"[FAIL] 4. Quote creation returned {st}: {res}")
        sys.exit(1)

    # Step 5: Approve Quote (Consent Snapshot)
    st, res = request("POST", "/api/approvals", headers=user_headers, body={"quote_id": quote_id})
    if st == 201 and "approval_id" in res:
        approval_id = res["approval_id"]
        print(f"[PASS] 5. Approval snapshot created: {approval_id}")
    else:
        print(f"[FAIL] 5. Approval snapshot returned {st}: {res}")
        sys.exit(1)

    # Step 6: Pre-Flight Validate (Dry Run)
    st, res = request("POST", f"/api/approvals/{approval_id}/validate", headers=user_headers)
    if st == 200 and res.get("allowed") is True and res.get("reason_code") == "OK":
        print("[PASS] 6. Pre-flight validation passed (allowed=True)")
    else:
        print(f"[FAIL] 6. Pre-flight validation returned {st}: {res}")
        sys.exit(1)

    # Step 7: Merchant Demo PATCH & Requote flow
    if ADMIN_TOKEN:
        print("--- Running Demo Invalidation & Reapproval Checks ---")
        # Spike price
        st, res = request(
            "PATCH",
            "/api/demo/products/laptop_b",
            headers=admin_headers,
            body={"price_minor": 55000},
        )
        if st == 200 and res.get("material") is True:
            print("[PASS] 7a. Demo merchant price spiked to $550 (material=True)")
        else:
            print(f"[FAIL] 7a. Demo PATCH returned {st}: {res}")
            sys.exit(1)

        # Validate again -> must block!
        st, res = request("POST", f"/api/approvals/{approval_id}/validate", headers=user_headers)
        if st == 200 and res.get("allowed") is False:
            print(f"[PASS] 7b. Pre-flight blocked as expected: reason={res.get('reason_code')}")
        else:
            print(f"[FAIL] 7b. Expected block but got: {res}")
            sys.exit(1)

        # Requote -> blocked by budget
        st, res = request("POST", f"/api/quotes/{quote_id}/requote", headers=user_headers)
        if st == 400 and res.get("error", {}).get("code") == "PRODUCT_NOT_ELIGIBLE":
            print("[PASS] 7c. Requote blocked by budget (requires_requirement_change=True)")
        else:
            print(f"[FAIL] 7c. Requote did not block as expected: {res}")
            sys.exit(1)

        # Buyer raises budget
        st, res = request(
            "POST",
            f"/api/intents/{intent_id}/requirements",
            headers=user_headers,
            body={"max_total_minor": 60000},
        )
        if st == 201 and res.get("previous_intent_id") == intent_id:
            print(f"[PASS] 7d. Buyer raised budget; chained intent {res.get('intent_id')}")
        else:
            print(f"[FAIL] 7d. Requirements update failed: {res}")
            sys.exit(1)

        # Timeline check
        st, res = request("GET", f"/api/quotes/{quote_id}/events", headers=user_headers)
        if st == 200 and len(res.get("events", [])) >= 3:
            print(f"[PASS] 7e. Event timeline verified ({len(res.get('events'))} events)")
        else:
            print(f"[FAIL] 7e. Timeline lookup failed: {res}")
            sys.exit(1)

        # Reset catalog back to pristine state
        st, res = request("POST", "/api/demo/reset", headers=admin_headers)
        if st == 200 and res.get("status") == "ok":
            print("[PASS] 7f. Demo catalog reset to initial state")
        else:
            print(f"[FAIL] 7f. Demo reset failed: {res}")
            sys.exit(1)

    print("=== All Smoke Tests Passed Successfully ===")


if __name__ == "__main__":
    main()
