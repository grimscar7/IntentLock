"""PayPal Sandbox payment routes.

Call order from backend/docs/CONTRACTS.md:
  validate_before_create -> create PayPal order -> buyer approves in PayPal
  -> validate_before_capture -> capture -> consume_approval

Sub-states live in the existing PaymentAttempt columns (no enum changes):
  INITIATED                          PayPal order created, waiting for buyer / capture
  PENDING + last_error_code None     capture request in progress
  PENDING + PAYMENT_STATUS_UNKNOWN   outcome unknown: reconcile, NEVER start a new payment
  CAPTURED / FAILED / CANCELLED      final
"""
import logging
import uuid

import httpx
from backend.api.auth import get_current_user
from backend.api.config import get_settings
from backend.api.consent.service import (
    consume_approval,
    validate_before_capture,
    validate_before_create,
)
from backend.api.database import get_db
from backend.api.errors import AppError
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.enums import PaymentAttemptStatus as S
from backend.api.models.payment_attempt import PaymentAttempt
from backend.api.payments import paypal_client as pp
from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy import select, update
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/payments", tags=["Payments"])

UNKNOWN = "PAYMENT_STATUS_UNKNOWN"


class CreatePaymentRequest(BaseModel):
    approval_id: str  # only an ID. The browser never sends a price or a merchant.


# ---------------------------------------------------------------- helpers
def _blocked(v) -> AppError:
    """Turn a failed ValidationResult into the standard API error (nothing is paid)."""
    code = getattr(v.reason_code, "value", str(v.reason_code))
    return AppError(
        code=code,
        message=v.message,
        status_code=409,
        details={
            "requires_new_approval": v.requires_new_approval,
            "changes": [c.model_dump() for c in v.changes],
            "retryable": False,
        },
    )


def _load_attempt(db: Session, attempt_id: str, user_id: str) -> PaymentAttempt:
    a = db.get(PaymentAttempt, attempt_id)
    if a is None or a.user_id != user_id:  # same answer for "missing" and "not yours"
        raise AppError(code="PAYMENT_NOT_FOUND", message="Payment attempt not found.", status_code=404)
    return a


def _load_approval(db: Session, approval_id: str) -> ApprovalSnapshot | None:
    return db.execute(
        select(ApprovalSnapshot).where(ApprovalSnapshot.approval_id == approval_id)
    ).scalar_one_or_none()


def _state(a: PaymentAttempt) -> str:
    """Names the frontend already understands."""
    if a.status == S.INITIATED:
        return "PAYPAL_ORDER_CREATED" if a.paypal_order_id else "DRAFT"
    if a.status == S.PENDING:
        return UNKNOWN if a.last_error_code == UNKNOWN else "CAPTURE_IN_PROGRESS"
    if a.status == S.CAPTURED:
        return "PAID"
    if a.status == S.CANCELLED:
        return "CANCELLED"
    return "FAILED"


def _view(db: Session, a: PaymentAttempt) -> dict:
    approval = _load_approval(db, a.approval_id)
    return {
        "attempt_id": a.payment_attempt_id,
        "approval_id": a.approval_id,
        "state": _state(a),
        "status": a.status.value,
        "paypal_order_id": a.paypal_order_id,
        "capture_id": a.paypal_capture_id,
        "amount": {"amount_minor": a.total_minor, "currency": a.currency},
        "total_minor": a.total_minor,
        "currency": a.currency,
        "merchant_name": approval.merchant_id if approval else None,
        "last_error_code": a.last_error_code,
        "captured_at": a.updated_at.isoformat() if a.status == S.CAPTURED and a.updated_at else None,
    }


def _extract_capture(order: dict) -> tuple[str | None, str | None]:
    try:
        cap = order["purchase_units"][0]["payments"]["captures"][0]
        return cap["id"], cap["status"]
    except (KeyError, IndexError, TypeError):
        return None, None


def _pending_error(a: PaymentAttempt) -> AppError:
    if a.last_error_code == UNKNOWN:
        return AppError(code=UNKNOWN, status_code=409, details={"retryable": True},
                        message="We could not confirm the payment yet. Do not pay again.")
    return AppError(code="CAPTURE_IN_PROGRESS", status_code=409, details={"retryable": True},
                    message="Payment is being processed.")


def _mark_unknown(db: Session, a: PaymentAttempt) -> AppError:
    """Outcome unknown (timeout, PayPal 5xx, pending capture). Keep it, reconcile later."""
    a.status = S.PENDING
    a.last_error_code = UNKNOWN
    db.commit()
    return AppError(code=UNKNOWN, status_code=504, details={"retryable": True},
                    message="We could not confirm the payment yet. Do not pay again.")


def _finalize_paid(db: Session, a: PaymentAttempt, capture_id: str | None) -> None:
    a.status = S.CAPTURED
    a.paypal_capture_id = capture_id
    a.last_error_code = None
    db.commit()
    # Contract step 6: the approval can never be captured again.
    if not consume_approval(db, a.approval_id):
        logger.critical("Approval %s was already consumed when attempt %s was captured",
                        a.approval_id, a.payment_attempt_id)


def _reconcile(db: Session, a: PaymentAttempt) -> dict:
    """Ask PayPal what really happened and update our record. Never starts a new payment."""
    try:
        order = pp.get_order(a.paypal_order_id)
    except (pp.PayPalError, httpx.HTTPError):
        raise AppError(code="PAYPAL_UNREACHABLE", status_code=502, details={"retryable": True},
                       message="Still cannot reach PayPal. Try again shortly.")
    cap_id, cap_status = _extract_capture(order)
    if order.get("status") == "COMPLETED" and cap_status == "COMPLETED":
        _finalize_paid(db, a, cap_id)
    elif order.get("status") == "APPROVED":
        a.status = S.INITIATED  # capture never happened: safe to retry the SAME order
        a.last_error_code = None
        db.commit()
    return _view(db, a)


# ---------------------------------------------------------------- 1. CREATE
@router.post("/create")
def create_payment(body: CreatePaymentRequest, user_id: str = Depends(get_current_user),
                   db: Session = Depends(get_db)):
    # Duplicate-click protection: look at the latest attempt for this approval first.
    existing = db.execute(
        select(PaymentAttempt)
        .where(PaymentAttempt.user_id == user_id, PaymentAttempt.approval_id == body.approval_id)
        .order_by(PaymentAttempt.created_at.desc())
    ).scalars().first()
    if existing is not None and existing.status == S.CAPTURED:
        raise AppError(code="ALREADY_PAID", message="This purchase was already paid.",
                       status_code=409, details={"retryable": False})
    if existing is not None and existing.status == S.PENDING:
        raise _pending_error(existing)

    # Vishnu's check #1 (fails closed). Nothing is created at PayPal unless this passes.
    v = validate_before_create(db=db, user_id=user_id, approval_id=body.approval_id, record_audit=True)
    if not v.allowed:
        raise _blocked(v)

    if existing is not None and existing.status == S.INITIATED and existing.paypal_order_id:
        return {"attempt_id": existing.payment_attempt_id, "paypal_order_id": existing.paypal_order_id}

    approval = _load_approval(db, body.approval_id)
    if approval is None:  # cannot happen after a passed validation, but fail closed anyway
        raise _blocked(v)

    attempt_id = str(uuid.uuid4())
    attempt = PaymentAttempt(
        payment_attempt_id=attempt_id,
        user_id=user_id,
        approval_id=approval.approval_id,
        total_minor=approval.total_minor,  # server-owned values from the frozen approval
        currency=approval.currency,
        status=S.INITIATED,
        idempotency_key=f"create-{attempt_id}",
    )
    db.add(attempt)
    db.commit()

    title = (approval.approved_terms or {}).get("title", "IntentLock purchase")
    try:
        order = pp.create_order(
            amount_minor=approval.total_minor,
            currency=approval.currency,
            reference_id=approval.merchant_id,
            description=title,
            request_id=attempt.idempotency_key,
            custom_id=attempt_id,
        )
    except (pp.PayPalError, httpx.HTTPError) as e:
        logger.error("PayPal create failed: %r %s", e, getattr(e, "body", ""))
        attempt.status = S.FAILED
        attempt.last_error_code = "PAYPAL_CREATE_FAILED"
        db.commit()
        raise AppError(code="PAYPAL_CREATE_FAILED", status_code=502, details={"retryable": True},
                       message="Could not start PayPal checkout. Please try again.")

    attempt.paypal_order_id = order["id"]
    db.commit()
    return {"attempt_id": attempt_id, "paypal_order_id": order["id"]}


# ---------------------------------------------------------------- 2. CAPTURE
@router.post("/{attempt_id}/capture")
def capture_payment(attempt_id: str, user_id: str = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    a = _load_attempt(db, attempt_id, user_id)

    # Duplicate-safe: an already captured attempt just returns the saved result.
    if a.status == S.CAPTURED:
        return _view(db, a)
    if a.status == S.PENDING:
        raise _pending_error(a)
    if a.status != S.INITIATED or not a.paypal_order_id:
        raise AppError(code="INVALID_STATE", status_code=409, details={"retryable": False},
                       message=f"Cannot capture a payment in state {a.status.value}.")

    # Vishnu's check #2. The approval is read from the attempt row, never from the browser.
    v = validate_before_capture(db=db, user_id=user_id, payment_attempt_id=a.payment_attempt_id,
                                record_audit=True)
    if not v.allowed:
        a.status = S.FAILED
        a.last_error_code = getattr(v.reason_code, "value", str(v.reason_code))
        db.commit()
        raise _blocked(v)

    # Ask PayPal what it holds, then verify it is exactly what we approved.
    try:
        order = pp.get_order(a.paypal_order_id)
    except (pp.PayPalError, httpx.HTTPError):
        raise AppError(code="PAYPAL_UNREACHABLE", status_code=502, details={"retryable": True},
                       message="Could not verify the order with PayPal. Please try again.")

    approval = _load_approval(db, a.approval_id)
    unit = (order.get("purchase_units") or [{}])[0]
    amount = unit.get("amount") or {}
    problems = []
    if order.get("id") != a.paypal_order_id:
        problems.append("order id")
    if unit.get("custom_id") != a.payment_attempt_id:
        problems.append("payment attempt")
    if approval is None or unit.get("reference_id") != approval.merchant_id:
        problems.append("merchant")
    if amount.get("currency_code") != a.currency:
        problems.append("currency")
    if amount.get("value") != pp.minor_to_str(a.total_minor):
        problems.append("amount")
    if problems:
        a.status = S.FAILED
        a.last_error_code = "ORDER_MISMATCH"
        db.commit()
        raise AppError(code="ORDER_MISMATCH", status_code=409, details={"retryable": False},
                       message=f"PayPal order does not match the approved purchase ({', '.join(problems)}).")

    status = order.get("status")
    if status == "COMPLETED":  # a retry after we lost the first response
        cap_id, cap_status = _extract_capture(order)
        if cap_status == "COMPLETED":
            _finalize_paid(db, a, cap_id)
            return _view(db, a)
    if status != "APPROVED":
        raise AppError(code="ORDER_NOT_APPROVED", status_code=409,
                       details={"retryable": True, "paypal_status": status},
                       message="The buyer has not approved this PayPal order yet.")

    # Atomically claim the capture. Only ONE request can win this.
    claimed = db.execute(
        update(PaymentAttempt)
        .where(PaymentAttempt.payment_attempt_id == a.payment_attempt_id, PaymentAttempt.status == S.INITIATED)
        .values(status=S.PENDING, last_error_code=None)
    ).rowcount
    db.commit()
    if not claimed:
        raise AppError(code="CAPTURE_IN_PROGRESS", status_code=409, details={"retryable": True},
                       message="Payment is being processed.")
    db.refresh(a)

    try:
        result = pp.capture_order(a.paypal_order_id, request_id=f"capture-{a.payment_attempt_id}")
    except pp.PayPalError as e:
        if e.issue == "INSTRUMENT_DECLINED":
            a.status = S.INITIATED  # same order can be restarted with another funding source
            a.last_error_code = "INSTRUMENT_DECLINED"
            db.commit()
            raise AppError(code="INSTRUMENT_DECLINED", status_code=422, details={"retryable": True},
                           message="Your payment method was declined. Choose another and try again.")
        if e.issue == "ORDER_ALREADY_CAPTURED":
            _reconcile(db, a)
            if a.status == S.CAPTURED:
                return _view(db, a)
            raise _mark_unknown(db, a)
        logger.error("PayPal capture failed: %s %s", e.status_code, e.body)
        if e.status_code >= 500:
            raise _mark_unknown(db, a)
        a.status = S.FAILED
        a.last_error_code = f"PAYPAL_{e.status_code}"
        db.commit()
        raise AppError(code="CAPTURE_FAILED", status_code=502, details={"retryable": False},
                       message="Payment could not be completed. No money was taken.")
    except httpx.HTTPError:  # includes timeouts
        raise _mark_unknown(db, a)

    cap_id, cap_status = _extract_capture(result)
    if result.get("status") == "COMPLETED" and cap_status == "COMPLETED":
        _finalize_paid(db, a, cap_id)
        return _view(db, a)
    a.paypal_capture_id = cap_id  # e.g. PENDING review at PayPal: confirm later
    raise _mark_unknown(db, a)


# ---------------------------------------------------------------- 3. STATUS
@router.get("/{attempt_id}")
def get_payment(attempt_id: str, user_id: str = Depends(get_current_user), db: Session = Depends(get_db)):
    return _view(db, _load_attempt(db, attempt_id, user_id))


# ---------------------------------------------------------------- 4. RECONCILE
@router.post("/{attempt_id}/reconcile")
def reconcile_payment(attempt_id: str, user_id: str = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    a = _load_attempt(db, attempt_id, user_id)
    if a.status != S.PENDING or a.last_error_code != UNKNOWN:
        return _view(db, a)
    return _reconcile(db, a)


# ---------------------------------------------------------------- DEV TEST PAGE
@router.get("/dev/checkout", response_class=HTMLResponse, include_in_schema=False)
def dev_checkout():
    """Whole buyer journey + PayPal button in one page. Only in dev/demo environments."""
    from pathlib import Path

    s = get_settings()
    if s.ENV.lower() not in [e.lower() for e in s.DEMO_ALLOWED_ENVS]:
        raise AppError(code="NOT_FOUND", message="Endpoint not found", status_code=404)
    import os

    html = (Path(__file__).parent / "dev_checkout.html").read_text(encoding="utf-8")
    return (html.replace("__CLIENT_ID__", os.getenv("PAYPAL_CLIENT_ID", ""))
                .replace("__ADMIN_TOKEN__", s.DEMO_ADMIN_TOKEN))
