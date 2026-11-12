"""Catalog product querying, code-based constraint filtering, versioning, and invalidation."""

from typing import Any

from backend.api.audit.service import record_event
from backend.api.errors import AppError
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import ApprovalStatus, AuditEventType, QuoteStatus
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.scripts.seed import SEED_PRODUCTS
from sqlalchemy import select, update
from sqlalchemy.orm import Session

ALLOWED_MUTATION_FIELDS = {"price_minor", "return_days", "available"}


def is_change_material(
    old_pv: ProductVersion,
    new_price: int,
    new_returns: int,
    new_available: bool,
    policy_bumped: bool,
) -> bool:
    """Classify if catalog update is material (disadvantaging buyer or policy changed)."""
    if policy_bumped:
        return True
    if new_price > old_pv.price_minor:
        return True
    if new_returns < old_pv.return_days:
        return True
    if old_pv.available and not new_available:
        return True
    return False


def get_current_products(db: Session) -> list[ProductVersion]:
    """Retrieve all current, available catalog product versions."""
    stmt = (
        select(ProductVersion)
        .where(
            ProductVersion.is_current.is_(True),
            ProductVersion.available.is_(True),
        )
        .order_by(ProductVersion.price_minor.asc())
    )
    return list(db.execute(stmt).scalars().all())


def evaluate_product_eligibility(
    product: ProductVersion, requirements: dict[str, Any]
) -> tuple[bool, dict[str, bool], list[str]]:
    """Evaluate all eligibility conditions for a single product with zero silent passes."""
    checks: dict[str, bool] = {}
    reasons: list[str] = []

    # 1. Availability and currency
    checks["is_current"] = bool(product.is_current)
    if not checks["is_current"]:
        reasons.append("Product version is outdated")

    checks["available"] = bool(product.available)
    if not checks["available"]:
        reasons.append("Product is not available in inventory")

    # 2. Currency check
    req_curr = requirements.get("currency")
    checks["currency_match"] = bool(req_curr and product.currency == req_curr)
    if not checks["currency_match"]:
        reasons.append(f"Currency mismatch ({product.currency} vs requested {req_curr})")

    # 3. Budget / price check
    req_budget = requirements.get("max_total_minor")
    checks["within_budget"] = bool(
        req_budget is not None
        and product.price_minor is not None
        and product.price_minor <= req_budget
    )
    if not checks["within_budget"]:
        reasons.append(f"Price (${product.price_minor/100:.2f}) exceeds budget (${(req_budget or 0)/100:.2f})")

    # 4. RAM check
    req_ram = requirements.get("minimum_ram_gb", 0)
    checks["ram_satisfaction"] = bool(
        product.ram_gb is not None and product.ram_gb >= req_ram
    )
    if not checks["ram_satisfaction"]:
        reasons.append(f"RAM ({product.ram_gb}GB) below required minimum ({req_ram}GB)")

    # 5. Return days check (NULL/missing field never silently satisfies requirement)
    req_returns = requirements.get("minimum_return_days", 0)
    checks["return_days_satisfaction"] = bool(
        product.return_days is not None and product.return_days >= req_returns
    )
    if not checks["return_days_satisfaction"]:
        reasons.append(
            f"Return policy ({product.return_days} days) below required minimum ({req_returns} days)"
        )

    # 6. Category match
    req_category = requirements.get("category", "")
    checks["category_match"] = bool(
        req_category and "laptop" in req_category.lower() and "laptop" in product.product_id.lower()
    )
    if not checks["category_match"]:
        reasons.append(f"Product does not match requested category ({req_category})")

    is_eligible = all(checks.values())
    return is_eligible, checks, reasons


def filter_products(
    requirements: dict[str, Any], db: Session
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Filter catalog products using strict code-based rules. Returns (eligible, rejected)."""
    products = get_current_products(db)
    eligible: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []

    for p in products:
        is_eligible, checks, reasons = evaluate_product_eligibility(p, requirements)
        prod_dict = {
            "product_id": p.product_id,
            "product_version_id": p.product_version_id,
            "title": p.title,
            "price_minor": p.price_minor,
            "currency": p.currency,
            "ram_gb": p.ram_gb,
            "return_days": p.return_days,
            "merchant_id": p.merchant_id,
            "policy_version": p.policy_version,
            "checks": checks,
            "reasons": reasons,
        }
        if is_eligible:
            eligible.append(prod_dict)
        else:
            rejected.append(prod_dict)

    return eligible, rejected


def create_product_version(
    db: Session,
    product_id: str,
    changes: dict[str, Any],
    actor: str = "demo_admin",
) -> dict[str, Any]:
    """Atomically create a new immutable ProductVersion and invalidate affected quotes and approvals."""
    # 1. Reject unknown fields
    unknown = set(changes.keys()) - ALLOWED_MUTATION_FIELDS
    if unknown:
        raise AppError(
            code="VALIDATION_ERROR",
            message=f"Disallowed fields in update: {', '.join(sorted(unknown))}",
            status_code=400,
        )

    # 2. Validate types and bounds
    for k, v in changes.items():
        if v is not None:
            if k in ("price_minor", "return_days"):
                if not isinstance(v, int) or isinstance(v, bool):
                    raise AppError("VALIDATION_ERROR", f"{k} must be an integer", status_code=400)
                if v < 0:
                    raise AppError("VALIDATION_ERROR", f"{k} must be non-negative", status_code=400)
            elif k == "available":
                if not isinstance(v, bool):
                    raise AppError("VALIDATION_ERROR", f"{k} must be a boolean", status_code=400)

    # 3. Lock current row in atomic transaction
    stmt = (
        select(ProductVersion)
        .where(ProductVersion.product_id == product_id, ProductVersion.is_current.is_(True))
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    current_pv = db.execute(stmt).scalar_one_or_none()
    if not current_pv:
        raise AppError("PRODUCT_NOT_FOUND", f"Product '{product_id}' not found", status_code=404)

    # 4. Check for actual changes
    old_price = current_pv.price_minor
    old_returns = current_pv.return_days
    old_available = current_pv.available

    new_price = changes.get("price_minor", old_price)
    new_returns = changes.get("return_days", old_returns)
    new_available = changes.get("available", old_available)

    if new_price == old_price and new_returns == old_returns and new_available == old_available:
        return {
            "product_id": current_pv.product_id,
            "old_version": current_pv.version,
            "new_version": current_pv.version,
            "changes": [],
            "material": False,
            "invalidated_quotes": 0,
            "invalidated_approvals": 0,
        }

    # 5. Build change details
    change_list: list[dict[str, Any]] = []
    if new_price != old_price:
        change_list.append({"field": "price_minor", "old": old_price, "new": new_price})
    if new_returns != old_returns:
        change_list.append({"field": "return_days", "old": old_returns, "new": new_returns})
    if new_available != old_available:
        change_list.append({"field": "available", "old": old_available, "new": new_available})

    # Policy version bumps ONLY on return_days change
    policy_bumped = (new_returns != old_returns)
    new_policy_version = current_pv.policy_version + (1 if policy_bumped else 0)
    if policy_bumped:
        change_list.append(
            {"field": "policy_version", "old": current_pv.policy_version, "new": new_policy_version}
        )

    material = is_change_material(current_pv, new_price, new_returns, new_available, policy_bumped)
    new_version_num = current_pv.version + 1
    new_pv_id = f"{product_id}_v{new_version_num}"

    # 6. Insert new version and flip is_current
    new_pv = ProductVersion(
        product_version_id=new_pv_id,
        product_id=current_pv.product_id,
        version=new_version_num,
        merchant_id=current_pv.merchant_id,
        title=current_pv.title,
        price_minor=new_price,
        currency=current_pv.currency,
        ram_gb=current_pv.ram_gb,
        return_days=new_returns,
        policy_version=new_policy_version,
        available=new_available,
        is_current=True,
    )
    current_pv.is_current = False
    db.add(new_pv)
    db.flush()

    invalidated_quotes_count = 0
    invalidated_approvals_count = 0

    # 7. If material, atomically invalidate affected active quotes and approvals
    if material:
        # Invalidate quotes
        quote_stmt = (
            select(Quote)
            .where(
                Quote.product_version_id == current_pv.product_version_id,
                Quote.status.in_([QuoteStatus.ACTIVE, QuoteStatus.APPROVED]),
            )
        )
        affected_quotes = db.execute(quote_stmt).scalars().all()
        for q in affected_quotes:
            q.status = QuoteStatus.INVALIDATED
            invalidated_quotes_count += 1
            record_event(
                db=db,
                event_type=AuditEventType.PRODUCT_VERSION_CHANGED,
                quote_id=q.quote_id,
                payload={
                    "product_id": product_id,
                    "old_version": current_pv.version,
                    "new_version": new_version_num,
                    "changes": change_list,
                },
            )

        # Invalidate approvals (CONSUMED stays CONSUMED)
        app_stmt = (
            select(ApprovalSnapshot)
            .where(
                ApprovalSnapshot.product_id == product_id,
                ApprovalSnapshot.product_version_id == current_pv.product_version_id,
                ApprovalSnapshot.status == ApprovalStatus.ACTIVE,
            )
        )
        affected_approvals = db.execute(app_stmt).scalars().all()
        for a in affected_approvals:
            a.status = ApprovalStatus.INVALIDATED
            invalidated_approvals_count += 1
            record_event(
                db=db,
                event_type=AuditEventType.APPROVAL_INVALIDATED,
                quote_id=a.quote_id,
                approval_id=a.approval_id,
                payload={
                    "product_id": product_id,
                    "reason": "material_product_change",
                    "changes": change_list,
                },
            )

    db.commit()
    db.refresh(new_pv)

    return {
        "product_id": product_id,
        "old_version": current_pv.version,
        "new_version": new_version_num,
        "changes": change_list,
        "material": material,
        "invalidated_quotes": invalidated_quotes_count,
        "invalidated_approvals": invalidated_approvals_count,
    }


def reset_demo_catalog(db: Session) -> int:
    """Restore the 5 seeded laptops to version 1 as current. Preserves quotes and audit history."""
    # Deactivate all versions > 1
    db.execute(
        update(ProductVersion)
        .where(ProductVersion.version > 1)
        .values(is_current=False)
    )

    # Restore v1 rows with baseline seed values
    seed_map = {item["product_id"]: item for item in SEED_PRODUCTS}
    v1_rows = db.execute(
        select(ProductVersion).where(ProductVersion.version == 1)
    ).scalars().all()

    reset_count = 0
    for row in v1_rows:
        base = seed_map.get(row.product_id)
        if base:
            row.price_minor = int(str(base["price_minor"]))
            row.return_days = int(str(base["return_days"]))
            row.available = True
            row.is_current = True
            row.policy_version = 1
            reset_count += 1

    db.commit()
    return reset_count
