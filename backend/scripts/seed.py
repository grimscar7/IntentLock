"""Idempotent seed script inserting 5 initial laptop ProductVersion rows."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.database import SessionLocal
from backend.api.models import ProductVersion

SEED_PRODUCTS = [
    {
        "product_id": "laptop_a",
        "title": "Refurbished Laptop A",
        "price_minor": 42000,
        "ram_gb": 8,
        "return_days": 14,
    },
    {
        "product_id": "laptop_b",
        "title": "Refurbished Laptop B",
        "price_minor": 48000,
        "ram_gb": 16,
        "return_days": 30,
    },
    {
        "product_id": "laptop_c",
        "title": "Refurbished Laptop C",
        "price_minor": 52000,
        "ram_gb": 16,
        "return_days": 30,
    },
    {
        "product_id": "laptop_d",
        "title": "Refurbished Laptop D",
        "price_minor": 45000,
        "ram_gb": 16,
        "return_days": 14,
    },
    {
        "product_id": "laptop_e",
        "title": "Refurbished Laptop E",
        "price_minor": 39000,
        "ram_gb": 8,
        "return_days": 30,
    },
]

MERCHANT_ID = "demo_store"
CURRENCY = "USD"
VERSION = 1
POLICY_VERSION = 1


def seed_products(db: Session) -> int:
    """Seed catalog products idempotently. Returns count of newly inserted rows."""
    inserted_count = 0

    for item in SEED_PRODUCTS:
        stmt = select(ProductVersion).where(
            ProductVersion.product_id == item["product_id"],
            ProductVersion.version == VERSION,
        )
        existing = db.execute(stmt).scalar_one_or_none()

        if existing is None:
            pv = ProductVersion(
                product_version_id=f"{item['product_id']}_v{VERSION}",
                product_id=item["product_id"],
                version=VERSION,
                merchant_id=MERCHANT_ID,
                title=item["title"],
                price_minor=item["price_minor"],
                currency=CURRENCY,
                ram_gb=item["ram_gb"],
                return_days=item["return_days"],
                policy_version=POLICY_VERSION,
                available=True,
                is_current=True,
            )
            db.add(pv)
            inserted_count += 1

    db.commit()

    total_current = db.execute(
        select(ProductVersion).where(ProductVersion.is_current.is_(True))
    ).scalars().all()

    print(
        f"Seed completed: {inserted_count} new products inserted. "
        f"Total current product versions: {len(total_current)}"
    )
    return inserted_count


def main() -> None:
    """Execute seed operation within a managed session."""
    db = SessionLocal()
    try:
        seed_products(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
