"""Verification script running the exact end-to-end shopping flow against uvicorn."""

import subprocess
import sys
import time

import httpx


def main():
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api.main:app", "--port", "8000"])
    try:
        client = httpx.Client(base_url="http://127.0.0.1:8000", headers={"X-User-Id": "demo_user"}, timeout=10.0)
        # Wait for server startup
        started = False
        for _ in range(20):
            try:
                res = client.get("/health")
                if res.status_code == 200:
                    started = True
                    break
            except Exception:
                time.sleep(0.5)

        if not started:
            raise RuntimeError("Uvicorn server failed to start within 10 seconds")

        # 1. POST /api/intents
        r1 = client.post(
            "/api/intents",
            json={"raw_text": "Looking for a refurbished laptop under $500 with 16GB RAM and 30-day returns"},
        )
        print("1. POST /api/intents ->", r1.status_code, r1.json())
        assert r1.status_code == 201
        intent_id = r1.json()["intent_id"]

        # 2. GET /api/intents/{id}/options
        r2 = client.get(f"/api/intents/{intent_id}/options")
        print("2. GET /api/intents/{id}/options ->", r2.status_code, r2.json())
        assert r2.status_code == 200
        eligible = r2.json()["eligible_products"]
        assert len(eligible) == 1
        assert eligible[0]["product_id"] == "laptop_b"

        # 3. POST /api/quotes
        r3 = client.post(
            "/api/quotes",
            json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
        )
        print("3. POST /api/quotes ->", r3.status_code, r3.json())
        assert r3.status_code == 201
        quote_id = r3.json()["quote_id"]
        assert r3.json()["total_minor"] == 48000

        # 4. POST /api/approvals (first)
        r4 = client.post("/api/approvals", json={"quote_id": quote_id})
        print("4. POST /api/approvals (first) ->", r4.status_code, r4.json())
        assert r4.status_code == 201
        approval_id_1 = r4.json()["approval_id"]

        # 5. POST /api/approvals (repeat)
        r5 = client.post("/api/approvals", json={"quote_id": quote_id})
        print("5. POST /api/approvals (repeat) ->", r5.status_code, r5.json())
        assert r5.status_code == 201
        approval_id_2 = r5.json()["approval_id"]

        assert approval_id_1 == approval_id_2
        print(f"\nIDEMPOTENCY VERIFIED: Repeated approval returned identical approval_id: {approval_id_1}")
        print("\nALL E2E VERIFICATION CHECKS PASSED SUCCESSFULLY!")
    finally:
        proc.terminate()

if __name__ == "__main__":
    main()
