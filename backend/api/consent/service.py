"""Consent verification, immutable approval snapshots, and pre-flight validation services."""

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.api.audit.service import record_event
from backend.api.catalog.service import evaluate_product_eligibility
from backend.api.config import get_settings
from backend.api.consent.comparator import compare
from backend.api.consent.explain import build_message
from backend.api.errors import AppError
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import (
    ApprovalStatus,
    AuditEventType,
    QuoteStatus,
    ReasonCode,
)
from backend.api.models.intent import Intent
from backend.api.models.payment_attempt import PaymentAttempt
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.api.schemas.validation import Change, ValidationResult
from sqlalchemy import select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def create_approval(user_id: str, quote_id: str, db: Session) -> ApprovalSnapshot:
    """Validate and atomically freeze quote terms into an immutable ApprovalSnapshot."""
    settings = get_settings()

    stmt = select(Quote).where(Quote.quote_id == quote_id)
    if db.bind and db.bind.dialect.name == "postgresql":
        stmt = stmt.with_for_update()

    quote = db.execute(stmt).scalar_one_or_none()
    if not quote:
        raise AppError("QUOTE_NOT_FOUND", f"Quote '{quote_id}' not found", status_code=404)

    if quote.user_id != user_id:
        raise AppError("FORBIDDEN", "You do not have permission to approve this quote", status_code=403)

    if quote.status == QuoteStatus.APPROVED:
        existing_approval = db.execute(
            select(ApprovalSnapshot)
            .where(
                ApprovalSnapshot.quote_id == quote_id,
                ApprovalSnapshot.user_id == user_id,
            )
            .order_by(ApprovalSnapshot.created_at.desc())
        ).scalars().first()
        if existing_approval:
            return existing_approval

    if quote.status != QuoteStatus.ACTIVE:
        raise AppError(
            code="QUOTE_NOT_ACTIVE",
            message=f"Quote status is {quote.status.value}, cannot be approved",
            status_code=400,
        )

    now = datetime.now(UTC)
    expires_at = quote.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)

    if expires_at <= now:
        quote.status = QuoteStatus.EXPIRED
        db.commit()
        raise AppError("QUOTE_EXPIRED", "Quote has expired and can no longer be approved", status_code=400)

    pv_stmt = select(ProductVersion).where(
        ProductVersion.product_version_id == quote.product_version_id
    )
    product_version = db.execute(pv_stmt).scalar_one_or_none()
    if not product_version or not product_version.is_current or not product_version.available:
        raise AppError(
            code="QUOTE_NO_LONGER_VALID",
            message="Product version has changed or is no longer available in the catalog",
            status_code=400,
        )

    intent = db.execute(
        select(Intent).where(Intent.intent_id == quote.intent_id)
    ).scalar_one_or_none()
    if not intent:
        raise AppError("QUOTE_NO_LONGER_VALID", "Underlying buyer intent not found", status_code=400)

    reqs = intent.requirements or {}
    is_eligible, _, reasons = evaluate_product_eligibility(product_version, reqs)
    if not is_eligible or quote.total_minor > reqs.get("max_total_minor", float("inf")):
        raise AppError(
            code="QUOTE_NO_LONGER_VALID",
            message="Quote terms no longer satisfy buyer intent requirements",
            status_code=400,
            details={"reasons": reasons},
        )

    db.execute(
        update(ApprovalSnapshot)
        .where(
            ApprovalSnapshot.user_id == user_id,
            ApprovalSnapshot.product_id == product_version.product_id,
            ApprovalSnapshot.status == ApprovalStatus.ACTIVE,
        )
        .values(status=ApprovalStatus.SUPERSEDED)
    )

    approval_expires_at = now + timedelta(minutes=settings.APPROVAL_TTL_MINUTES)
    approved_terms = {
        "title": product_version.title,
        "product_id": product_version.product_id,
        "product_version_id": quote.product_version_id,
        "quantity": quote.quantity,
        "unit_price_minor": quote.unit_price_minor,
        "shipping_minor": quote.shipping_minor,
        "tax_minor": quote.tax_minor,
        "total_minor": quote.total_minor,
        "currency": quote.currency,
        "ram_gb": product_version.ram_gb,
        "return_days": product_version.return_days,
        "budget_minor": intent.requirements.get("max_total_minor"),
    }

    approval = ApprovalSnapshot(
        user_id=user_id,
        quote_id=quote.quote_id,
        merchant_id=quote.merchant_id,
        product_id=product_version.product_id,
        product_version_id=quote.product_version_id,
        quantity=quote.quantity,
        total_minor=quote.total_minor,
        currency=quote.currency,
        policy_version=product_version.policy_version,
        approved_terms=approved_terms,
        approved_at=now,
        expires_at=approval_expires_at,
        status=ApprovalStatus.ACTIVE,
    )
    db.add(approval)
    db.flush()

    quote.status = QuoteStatus.APPROVED

    record_event(
        db=db,
        event_type=AuditEventType.APPROVAL_GRANTED,
        quote_id=quote.quote_id,
        approval_id=approval.approval_id,
        payload={
            "user_id": user_id,
            "product_id": product_version.product_id,
            "product_version_id": product_version.product_version_id,
            "total_minor": quote.total_minor,
            "currency": quote.currency,
            "approved_at": now.isoformat(),
            "expires_at": approval_expires_at.isoformat(),
        },
    )

    db.commit()
    db.refresh(approval)
    return approval


def consume_approval(db: Session, approval_id: str) -> bool:
    """Atomically consume an active approval snapshot after verified capture. Returns True once."""
    stmt = (
        update(ApprovalSnapshot)
        .where(
            ApprovalSnapshot.approval_id == approval_id,
            ApprovalSnapshot.status == ApprovalStatus.ACTIVE,
        )
        .values(status=ApprovalStatus.CONSUMED)
    )
    result = db.execute(stmt)
    db.commit()
    return bool(getattr(result, "rowcount", 0) > 0)


def _validate_approval_consistency(
    db: Session,
    user_id: str,
    approval_id: str,
    internal_call_context: str,
) -> tuple[bool, ReasonCode, list[Change], bool, str | None, str | None, dict[str, Any]]:
    """Shared internal validation logic for pre-create and pre-capture checks."""
    settings = get_settings()
    audit_extra: dict[str, Any] = {}

    stmt = select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)
    approval = db.execute(stmt).scalar_one_or_none()

    if not approval:
        audit_extra["internal_rejection"] = "approval_missing"
        return False, ReasonCode.APPROVAL_NOT_FOUND, [], False, None, None, audit_extra

    if approval.user_id != user_id:
        audit_extra["internal_rejection"] = f"user_mismatch (owner={approval.user_id}, caller={user_id})"
        return False, ReasonCode.APPROVAL_NOT_FOUND, [], False, approval.approval_id, approval.quote_id, audit_extra

    quote_id = approval.quote_id

    # Check status
    if approval.status == ApprovalStatus.CONSUMED:
        return False, ReasonCode.APPROVAL_ALREADY_CONSUMED, [], True, approval.approval_id, quote_id, audit_extra
    if approval.status == ApprovalStatus.SUPERSEDED:
        return False, ReasonCode.APPROVAL_SUPERSEDED, [], True, approval.approval_id, quote_id, audit_extra
    if approval.status == ApprovalStatus.INVALIDATED:
        return False, ReasonCode.APPROVAL_INVALIDATED, [], True, approval.approval_id, quote_id, audit_extra
    if approval.status == ApprovalStatus.EXPIRED:
        return False, ReasonCode.APPROVAL_EXPIRED, [], True, approval.approval_id, quote_id, audit_extra
    if approval.status != ApprovalStatus.ACTIVE:
        return False, ReasonCode.APPROVAL_NOT_ACTIVE, [], True, approval.approval_id, quote_id, audit_extra

    # Check expiration timestamp against UTC now
    now = datetime.now(UTC)
    app_exp = approval.expires_at
    if app_exp.tzinfo is None:
        app_exp = app_exp.replace(tzinfo=UTC)

    if app_exp <= now:
        approval.status = ApprovalStatus.EXPIRED
        db.commit()
        return False, ReasonCode.APPROVAL_EXPIRED, [], True, approval.approval_id, quote_id, audit_extra

    # Load bound quote
    quote_stmt = select(Quote).where(Quote.quote_id == quote_id)
    quote = db.execute(quote_stmt).scalar_one_or_none()
    if not quote or quote.status != QuoteStatus.APPROVED:
        return False, ReasonCode.QUOTE_INVALID, [], True, approval.approval_id, quote_id, audit_extra

    # Load approved product version
    approved_pv_stmt = select(ProductVersion).where(
        ProductVersion.product_version_id == approval.product_version_id
    )
    approved_pv = db.execute(approved_pv_stmt).scalar_one_or_none()

    # Load current product version
    current_pv_stmt = select(ProductVersion).where(
        ProductVersion.product_id == approval.product_id,
        ProductVersion.is_current.is_(True),
    )
    current_pv = db.execute(current_pv_stmt).scalar_one_or_none()

    if not current_pv or not approved_pv:
        return False, ReasonCode.PRODUCT_VERSION_CHANGED, [], True, approval.approval_id, quote_id, audit_extra

    # Load intent requirements
    intent_stmt = select(Intent).where(Intent.intent_id == quote.intent_id)
    intent = db.execute(intent_stmt).scalar_one_or_none()
    requirements = intent.requirements if intent else {}

    # Run pure comparator
    changes, reason_code = compare(
        approval=approval,
        quote=quote,
        approved_pv=approved_pv,
        current_pv=current_pv,
        requirements=requirements,
        shipping_minor=settings.DEFAULT_SHIPPING_MINOR,
        tax_minor=settings.DEFAULT_TAX_MINOR,
    )

    is_allowed = (reason_code == ReasonCode.OK)
    requires_new = not is_allowed

    return is_allowed, reason_code, changes, requires_new, approval.approval_id, quote_id, audit_extra


def validate_before_create(
    db: Session,
    user_id: str,
    approval_id: str,
    *,
    record_audit: bool = True,
) -> ValidationResult:
    """Pre-flight check before creating a PayPal order. Fails closed without raising business exceptions."""
    checked_at = datetime.now(UTC)
    try:
        allowed, reason_code, changes, requires_new, resolved_app_id, quote_id, audit_extra = (
            _validate_approval_consistency(
                db=db,
                user_id=user_id,
                approval_id=approval_id,
                internal_call_context="pre_create",
            )
        )

        message = build_message(changes, reason_code)

        if record_audit:
            event_type = (
                AuditEventType.PRE_CREATE_VALIDATION_PASSED
                if allowed
                else AuditEventType.PRE_CREATE_VALIDATION_FAILED
            )
            payload = {
                "user_id": user_id,
                "approval_id": approval_id,
                "allowed": allowed,
                "reason_code": reason_code.value,
                "changes": [c.model_dump() for c in changes],
                **audit_extra,
            }
            record_event(
                db=db,
                event_type=event_type,
                quote_id=quote_id or "unbound",
                approval_id=resolved_app_id,
                payload=payload,
            )
            db.commit()

        return ValidationResult(
            allowed=allowed,
            reason_code=reason_code,
            changes=changes,
            requires_new_approval=requires_new,
            message=message,
            approval_id=resolved_app_id,
            quote_id=quote_id,
            checked_at=checked_at,
        )

    except Exception as e:
        logger.error("Unexpected exception in validate_before_create: %s", str(e))
        if record_audit:
            try:
                record_event(
                    db=db,
                    event_type=AuditEventType.PRE_CREATE_VALIDATION_FAILED,
                    quote_id="unbound",
                    approval_id=approval_id,
                    payload={"error": "VALIDATION_ERROR", "allowed": False},
                )
                db.commit()
            except Exception:
                db.rollback()

        return ValidationResult(
            allowed=False,
            reason_code=ReasonCode.VALIDATION_ERROR,
            changes=[],
            requires_new_approval=False,
            message="An unexpected validation error occurred. Payment creation was blocked for safety.",
            approval_id=approval_id,
            quote_id=None,
            checked_at=checked_at,
        )


def validate_before_capture(
    db: Session,
    user_id: str,
    payment_attempt_id: str,
    *,
    record_audit: bool = True,
) -> ValidationResult:
    """Pre-flight check before capturing PayPal payment. Never trusts client approval IDs."""
    checked_at = datetime.now(UTC)
    try:
        # 1. Resolve PaymentAttempt
        stmt = select(PaymentAttempt).where(PaymentAttempt.payment_attempt_id == payment_attempt_id)
        attempt = db.execute(stmt).scalar_one_or_none()

        if not attempt or attempt.user_id != user_id:
            if record_audit:
                record_event(
                    db=db,
                    event_type=AuditEventType.PRE_CAPTURE_VALIDATION_FAILED,
                    quote_id="unbound",
                    payment_attempt_id=payment_attempt_id,
                    payload={"reason_code": ReasonCode.PAYMENT_ATTEMPT_NOT_FOUND.value, "allowed": False},
                )
                db.commit()
            return ValidationResult(
                allowed=False,
                reason_code=ReasonCode.PAYMENT_ATTEMPT_NOT_FOUND,
                changes=[],
                requires_new_approval=False,
                message="Payment attempt not found or access denied.",
                approval_id=None,
                quote_id=None,
                checked_at=checked_at,
            )

        if not attempt.approval_id:
            if record_audit:
                record_event(
                    db=db,
                    event_type=AuditEventType.PRE_CAPTURE_VALIDATION_FAILED,
                    quote_id="unbound",
                    payment_attempt_id=payment_attempt_id,
                    payload={"reason_code": ReasonCode.PAYMENT_ATTEMPT_UNBOUND.value, "allowed": False},
                )
                db.commit()
            return ValidationResult(
                allowed=False,
                reason_code=ReasonCode.PAYMENT_ATTEMPT_UNBOUND,
                changes=[],
                requires_new_approval=True,
                message="Payment attempt does not have a bound approval.",
                approval_id=None,
                quote_id=None,
                checked_at=checked_at,
            )

        approval_id = attempt.approval_id

        # 2. Verify bound approval consistency
        allowed, reason_code, changes, requires_new, resolved_app_id, quote_id, audit_extra = (
            _validate_approval_consistency(
                db=db,
                user_id=user_id,
                approval_id=approval_id,
                internal_call_context="pre_capture",
            )
        )

        # 3. Check attempt amount and currency against approval
        if allowed:
            app_stmt = select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)
            app_snap = db.execute(app_stmt).scalar_one()

            if attempt.total_minor != app_snap.total_minor:
                allowed = False
                reason_code = ReasonCode.AMOUNT_MISMATCH
                requires_new = True
                changes.append(
                    Change(field="total_minor", approved=app_snap.total_minor, current=attempt.total_minor)
                )

            if attempt.currency != app_snap.currency:
                allowed = False
                reason_code = ReasonCode.CURRENCY_MISMATCH
                requires_new = True
                changes.append(
                    Change(field="currency", approved=app_snap.currency, current=attempt.currency)
                )

        message = build_message(changes, reason_code)

        if record_audit:
            event_type = (
                AuditEventType.PRE_CAPTURE_VALIDATION_PASSED
                if allowed
                else AuditEventType.PRE_CAPTURE_VALIDATION_FAILED
            )
            payload = {
                "user_id": user_id,
                "payment_attempt_id": payment_attempt_id,
                "approval_id": approval_id,
                "allowed": allowed,
                "reason_code": reason_code.value,
                "changes": [c.model_dump() for c in changes],
                **audit_extra,
            }
            record_event(
                db=db,
                event_type=event_type,
                quote_id=quote_id or "unbound",
                approval_id=resolved_app_id,
                payment_attempt_id=payment_attempt_id,
                payload=payload,
            )
            db.commit()

        return ValidationResult(
            allowed=allowed,
            reason_code=reason_code,
            changes=changes,
            requires_new_approval=requires_new,
            message=message,
            approval_id=resolved_app_id,
            quote_id=quote_id,
            checked_at=checked_at,
        )

    except Exception as e:
        logger.error("Unexpected exception in validate_before_capture: %s", str(e))
        if record_audit:
            try:
                record_event(
                    db=db,
                    event_type=AuditEventType.PRE_CAPTURE_VALIDATION_FAILED,
                    quote_id="unbound",
                    payment_attempt_id=payment_attempt_id,
                    payload={"error": "VALIDATION_ERROR", "allowed": False},
                )
                db.commit()
            except Exception:
                db.rollback()

        return ValidationResult(
            allowed=False,
            reason_code=ReasonCode.VALIDATION_ERROR,
            changes=[],
            requires_new_approval=False,
            message="An unexpected validation error occurred. Payment capture was blocked for safety.",
            approval_id=None,
            quote_id=None,
            checked_at=checked_at,
        )
