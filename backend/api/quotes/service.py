"""Quote creation, pricing calculation, eligibility verification, and retrieval."""

from datetime import UTC, datetime, timedelta

from backend.api.ai.service import get_ai_client
from backend.api.audit.service import record_event
from backend.api.catalog.service import evaluate_product_eligibility
from backend.api.config import get_settings
from backend.api.consent.explain import build_message
from backend.api.errors import AppError
from backend.api.intents.service import get_intent_by_id
from backend.api.models.enums import (
    AuditEventType,
    IntentStatus,
    QuoteStatus,
    ReasonCode,
)
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.api.schemas.demo import RequoteComparison
from backend.api.schemas.validation import Change
from sqlalchemy import select
from sqlalchemy.orm import Session


def create_quote(
    user_id: str,
    intent_id: str,
    product_id: str,
    quantity: int,
    db: Session,
) -> Quote:
    """Generate a binding quote after strict server-side eligibility check and integer pricing."""
    if quantity <= 0:
        raise AppError("INVALID_QUANTITY", "Quantity must be greater than zero", status_code=400)

    settings = get_settings()

    # 1. Load intent and verify ownership
    intent = get_intent_by_id(user_id=user_id, intent_id=intent_id, db=db)
    if intent.status != IntentStatus.VALIDATED:
        raise AppError(
            code="INTENT_NOT_VALIDATED",
            message=f"Intent must be in VALIDATED status to quote (current: {intent.status.value})",
            status_code=400,
        )

    # 2. Load CURRENT product version
    stmt = (
        select(ProductVersion)
        .where(
            ProductVersion.product_id == product_id,
            ProductVersion.is_current.is_(True),
            ProductVersion.available.is_(True),
        )
    )
    product = db.execute(stmt).scalar_one_or_none()
    if not product:
        raise AppError("PRODUCT_NOT_FOUND", f"Product '{product_id}' not found in active catalog", status_code=404)

    # 3. Re-verify eligibility against intent criteria
    is_eligible, checks, reasons = evaluate_product_eligibility(product, intent.requirements)
    if not is_eligible:
        raise AppError(
            code="PRODUCT_NOT_ELIGIBLE",
            message=f"Product '{product_id}' does not satisfy intent requirements",
            status_code=400,
            details={"failed_checks": reasons},
        )

    # 4. Integer pricing math strictly on server
    unit_price_minor = product.price_minor
    shipping_minor = settings.DEFAULT_SHIPPING_MINOR
    tax_minor = settings.DEFAULT_TAX_MINOR
    total_minor = (unit_price_minor * quantity) + shipping_minor + tax_minor

    # Verify total against budget if quantity is 1
    max_budget = intent.requirements.get("max_total_minor")
    if max_budget is not None and total_minor > max_budget:
        raise AppError(
            code="PRODUCT_NOT_ELIGIBLE",
            message=f"Calculated quote total ({total_minor}) exceeds intent budget ({max_budget})",
            status_code=400,
        )

    now = datetime.now(UTC)
    expires_at = now + timedelta(minutes=settings.QUOTE_TTL_MINUTES)

    quote = Quote(
        user_id=user_id,
        intent_id=intent.intent_id,
        product_version_id=product.product_version_id,
        merchant_id=product.merchant_id,
        quantity=quantity,
        unit_price_minor=unit_price_minor,
        shipping_minor=shipping_minor,
        tax_minor=tax_minor,
        total_minor=total_minor,
        currency=product.currency,
        status=QuoteStatus.ACTIVE,
        expires_at=expires_at,
    )
    db.add(quote)
    db.flush()

    # 5. Write QUOTE_CREATED audit event
    record_event(
        db=db,
        event_type=AuditEventType.QUOTE_CREATED,
        quote_id=quote.quote_id,
        payload={
            "user_id": user_id,
            "intent_id": intent.intent_id,
            "product_id": product_id,
            "product_version_id": product.product_version_id,
            "quantity": quantity,
            "total_minor": total_minor,
            "currency": product.currency,
            "expires_at": expires_at.isoformat(),
        },
    )

    db.commit()
    db.refresh(quote)
    return quote


def get_quote_by_id(user_id: str, quote_id: str, db: Session) -> tuple[Quote, bool]:
    """Retrieve quote by ID with ownership enforcement and computed expiration status."""
    stmt = select(Quote).where(Quote.quote_id == quote_id)
    quote = db.execute(stmt).scalar_one_or_none()
    if not quote:
        raise AppError("QUOTE_NOT_FOUND", f"Quote '{quote_id}' not found", status_code=404)
    if quote.user_id != user_id:
        raise AppError("FORBIDDEN", "You do not have access to this quote", status_code=403)

    now = datetime.now(UTC)
    expires_at = quote.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    is_expired = expires_at <= now or quote.status == QuoteStatus.EXPIRED

    return quote, is_expired


def requote(db: Session, user_id: str, quote_id: str) -> tuple[Quote, RequoteComparison]:
    """Generate a fresh quote for the current product version after merchant/catalog changes.

    If the current version is ineligible or exceeds the intent budget, raises AppError
    with code='PRODUCT_NOT_ELIGIBLE' and details={'requires_requirement_change': True, ...}.
    """
    settings = get_settings()

    # 1. Load old quote and verify ownership
    stmt = select(Quote).where(Quote.quote_id == quote_id)
    old_quote = db.execute(stmt).scalar_one_or_none()
    if not old_quote:
        raise AppError("QUOTE_NOT_FOUND", f"Quote '{quote_id}' not found", status_code=404)
    if old_quote.user_id != user_id:
        raise AppError("FORBIDDEN", "You do not have access to this quote", status_code=403)

    # 2. Load old product version
    old_pv_stmt = select(ProductVersion).where(
        ProductVersion.product_version_id == old_quote.product_version_id
    )
    old_pv = db.execute(old_pv_stmt).scalar_one_or_none()
    if not old_pv:
        raise AppError("PRODUCT_NOT_FOUND", "Original product version not found", status_code=404)

    # 3. Find current active product version
    curr_pv_stmt = select(ProductVersion).where(
        ProductVersion.product_id == old_pv.product_id,
        ProductVersion.is_current.is_(True),
    )
    current_pv = db.execute(curr_pv_stmt).scalar_one_or_none()
    if not current_pv or not current_pv.available:
        raise AppError("PRODUCT_NOT_AVAILABLE", "Product is no longer available in the catalog", status_code=400)

    # 4. Load intent
    intent = get_intent_by_id(user_id=user_id, intent_id=old_quote.intent_id, db=db)

    # 5. Check eligibility against intent
    is_eligible, checks, reasons = evaluate_product_eligibility(current_pv, intent.requirements)

    new_unit_price = current_pv.price_minor
    new_total = (new_unit_price * old_quote.quantity) + settings.DEFAULT_SHIPPING_MINOR + settings.DEFAULT_TAX_MINOR
    max_budget = intent.requirements.get("max_total_minor")
    budget_exceeded = max_budget is not None and new_total > max_budget

    # Build changes between old quote/pv and current
    changes: list[Change] = []
    if old_quote.total_minor != new_total:
        changes.append(Change(field="total_minor", approved=old_quote.total_minor, current=new_total))
    if old_pv.price_minor != current_pv.price_minor:
        changes.append(Change(field="price_minor", approved=old_pv.price_minor, current=current_pv.price_minor))
    if old_pv.return_days != current_pv.return_days:
        changes.append(Change(field="return_days", approved=old_pv.return_days, current=current_pv.return_days))
    if old_pv.policy_version != current_pv.policy_version:
        changes.append(Change(field="policy_version", approved=old_pv.policy_version, current=current_pv.policy_version))
    if old_pv.product_version_id != current_pv.product_version_id:
        changes.append(
            Change(
                field="product_version_id",
                approved=old_pv.product_version_id,
                current=current_pv.product_version_id,
            )
        )

    if not is_eligible or budget_exceeded:
        all_reasons = list(reasons)
        if budget_exceeded:
            all_reasons.append(f"Calculated quote total ({new_total}) exceeds intent budget ({max_budget})")
        raise AppError(
            code="PRODUCT_NOT_ELIGIBLE",
            message="Product exceeds intent criteria",
            status_code=400,
            details={
                "requires_requirement_change": True,
                "reasons": all_reasons,
                "changes": [c.model_dump() for c in changes],
            },
        )

    # 6. Create new quote
    new_quote = create_quote(
        user_id=user_id,
        intent_id=intent.intent_id,
        product_id=current_pv.product_id,
        quantity=old_quote.quantity,
        db=db,
    )

    # 7. Generate explanation
    base_message = build_message(changes, ReasonCode.QUOTE_CHANGED)
    ai_client = get_ai_client()
    changes_dict = [c.model_dump() for c in changes]
    message, source = ai_client.reword_change_explanation(changes_dict, base_message)

    comparison = RequoteComparison(
        old_total_minor=old_quote.total_minor,
        new_total_minor=new_quote.total_minor,
        changes=changes,
        base_message=base_message,
        message=message,
        explanation_source=source,
    )

    return new_quote, comparison
