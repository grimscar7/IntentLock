"""Live verification for Phase 4: validation pass and price change block."""

import subprocess
import sys
import time

import httpx
from sqlalchemy import create_engine, update
from sqlalchemy.orm import sessionmaker

from backend.api.config import get_settings
from backend.api.models.product_version import ProductVersion


def main():
    settings = get_settings()
    engine = create_engine(settings.DATABASE_URL)
    from backend.api.models import Base
    from backend.scripts.seed import seed_products

    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        seed_products(s)

    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.api.main:app", "--port", "8000"])
    try:
        client = httpx.Client(base_url="http://127.0.0.1:8000", headers={"X-User-Id": "demo_user"}, timeout=10.0)
        # Wait for uvicorn
        for _ in range(20):
            try:
                if client.get("/health").status_code == 200:
                    break
            except Exception:
                time.sleep(0.5)

        # 1. Intent
        r_intent = client.post(
            "/api/intents",
            json={"raw_text": "Looking for a refurbished laptop under $500 with 16GB RAM and 30-day returns"},
        )
        intent_id = r_intent.json()["intent_id"]

        # 2. Quote
        r_quote = client.post(
            "/api/quotes",
            json={"intent_id": intent_id, "product_id": "laptop_b", "quantity": 1},
        )
        quote_id = r_quote.json()["quote_id"]

        # 3. Approval
        r_appr = client.post("/api/approvals", json={"quote_id": quote_id})
        approval_id = r_appr.json()["approval_id"]

        # 4. Validate (Dry run preflight: should be allowed=True)
        r_val1 = client.post(f"/api/approvals/{approval_id}/validate")
        print("Initial Validation (Pass):", r_val1.status_code, r_val1.json())
        assert r_val1.json()["allowed"] is True
        assert r_val1.json()["reason_code"] == "OK"

        # 5. Simulate merchant change in DB: price increases $480 -> $540
        with Session() as db:
            db.execute(
                update(ProductVersion)
                .where(ProductVersion.product_id == "laptop_b")
                .values(is_current=False)
            )
            v2 = ProductVersion(
                product_version_id="laptop_b_v2_live",
                product_id="laptop_b",
                version=2,
                merchant_id="demo_store",
                title="Refurbished Laptop B",
                price_minor=54000,
                currency="USD",
                ram_gb=16,
                return_days=30,
                policy_version=1,
                available=True,
                is_current=True,
            )
            db.add(v2)
            db.commit()

        # 6. Validate again: should be blocked!
        r_val2 = client.post(f"/api/approvals/{approval_id}/validate")
        print("\nSecond Validation (Blocked):", r_val2.status_code, r_val2.json())
        assert r_val2.json()["allowed"] is False
        assert r_val2.json()["reason_code"] == "QUOTE_CHANGED"
        assert r_val2.json()["requires_new_approval"] is True
        assert "The approved price was $480.00. The current price is $540.00." in r_val2.json()["message"]

        print("\nPHASE 4 E2E VERIFICATION SUCCEEDED!")
    finally:
        proc.terminate()

if __name__ == "__main__":
    main()
