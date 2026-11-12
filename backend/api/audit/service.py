"""Append-only audit service for tracking lifecycle events and security validations."""

from typing import Any

from backend.api.consent.explain import format_minor_units
from backend.api.errors import AppError
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.audit_event import AuditEvent
from backend.api.models.enums import AuditEventType
from backend.api.models.payment_attempt import PaymentAttempt
from backend.api.models.quote import Quote
from backend.api.schemas.demo import TimelineItem, TimelineResponse
from sqlalchemy import or_, select
from sqlalchemy.orm import Session


def record_event(
    db: Session,
    event_type: AuditEventType,
    quote_id: str,
    approval_id: str | None = None,
    payment_attempt_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> AuditEvent:
    """Record an immutable, append-only audit event in the database."""
    event = AuditEvent(
        quote_id=quote_id,
        approval_id=approval_id,
        payment_attempt_id=payment_attempt_id,
        event_type=event_type,
        payload=payload if payload is not None else {},
    )
    db.add(event)
    db.flush()
    return event


def redact_sensitive(obj: Any) -> Any:
    """Recursively scrub tokens, secrets, credentials, and sensitive headers."""
    if isinstance(obj, dict):
        redacted = {}
        for k, v in obj.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("token", "secret", "key", "authorization", "password")):
                redacted[k] = "[REDACTED]"
            else:
                redacted[k] = redact_sensitive(v)
        return redacted
    elif isinstance(obj, list):
        return [redact_sensitive(item) for item in obj]
    return obj


def format_timeline_title(event_type: AuditEventType) -> str:
    """Generate concise human-facing label for audit event."""
    mapping = {
        AuditEventType.QUOTE_CREATED: "Quote Created",
        AuditEventType.APPROVAL_GRANTED: "Buyer Approved",
        AuditEventType.PRODUCT_VERSION_CHANGED: "Merchant Catalog Changed",
        AuditEventType.APPROVAL_INVALIDATED: "Approval Invalidated",
        AuditEventType.PRE_CREATE_VALIDATION_PASSED: "Validation Passed (Pre-Create)",
        AuditEventType.PRE_CREATE_VALIDATION_FAILED: "Validation Blocked (Pre-Create)",
        AuditEventType.PRE_CAPTURE_VALIDATION_PASSED: "Validation Passed (Pre-Capture)",
        AuditEventType.PRE_CAPTURE_VALIDATION_FAILED: "Validation Blocked (Pre-Capture)",
    }
    return mapping.get(event_type, event_type.value.replace("_", " ").title())


def format_timeline_description(event: AuditEvent) -> str:
    """Produce clear single-sentence summary of what took place from the event payload."""
    payload = event.payload or {}
    et = event.event_type

    if et == AuditEventType.QUOTE_CREATED:
        qty = payload.get("quantity", 1)
        cur = payload.get("currency", "USD")
        tot = payload.get("total_minor", 0)
        return f"Quote generated for {qty} item(s) at total {format_minor_units(tot, cur)}."

    if et == AuditEventType.APPROVAL_GRANTED:
        qid = payload.get("quote_id", event.quote_id)
        pid = payload.get("product_id", "")
        return f"Buyer approved terms for product '{pid}' on quote {qid}."

    if et == AuditEventType.PRODUCT_VERSION_CHANGED:
        pid = payload.get("product_id", "")
        v = payload.get("version", "")
        return f"Merchant updated product '{pid}' creating catalog version {v}."

    if et == AuditEventType.APPROVAL_INVALIDATED:
        reason = payload.get("reason", "catalog updates")
        return f"Approval {event.approval_id} was invalidated due to {reason}."

    if et == AuditEventType.PRE_CREATE_VALIDATION_PASSED:
        return "Pre-create validation succeeded against active approval snapshot."

    if et == AuditEventType.PRE_CREATE_VALIDATION_FAILED:
        code = payload.get("reason_code", "UNKNOWN")
        msg = payload.get("message", "Validation mismatch")
        return f"Pre-create validation blocked: {msg} (code: {code})."

    if et == AuditEventType.PRE_CAPTURE_VALIDATION_PASSED:
        return "Pre-capture validation succeeded; ready for payment capture."

    if et == AuditEventType.PRE_CAPTURE_VALIDATION_FAILED:
        code = payload.get("reason_code", "UNKNOWN")
        msg = payload.get("message", "Validation mismatch")
        return f"Pre-capture validation blocked: {msg} (code: {code})."

    return f"Lifecycle audit event recorded: {event.event_type.value}."


def get_quote_timeline(
    db: Session,
    user_id: str,
    quote_id: str,
    limit: int = 100,
    cursor: str | None = None,
) -> TimelineResponse:
    """Retrieve full chronological audit timeline for a quote, enforcing ownership and redaction."""
    # 1. Ownership check: return 404 if missing or foreign to avoid leaking existence
    stmt = select(Quote).where(Quote.quote_id == quote_id)
    quote = db.execute(stmt).scalar_one_or_none()
    if not quote or quote.user_id != user_id:
        raise AppError("QUOTE_NOT_FOUND", f"Quote '{quote_id}' not found", status_code=404)

    # 2. Gather approval IDs linked to this quote
    appr_stmt = select(ApprovalSnapshot.approval_id).where(ApprovalSnapshot.quote_id == quote_id)
    approval_ids = db.execute(appr_stmt).scalars().all()

    # 3. Gather payment attempt IDs linked to those approvals
    attempt_ids: list[str] = []
    if approval_ids:
        pa_stmt = select(PaymentAttempt.payment_attempt_id).where(
            PaymentAttempt.approval_id.in_(approval_ids)
        )
        attempt_ids = list(db.execute(pa_stmt).scalars().all())

    # 4. Query all relevant audit events
    conditions = [AuditEvent.quote_id == quote_id]
    if approval_ids:
        conditions.append(AuditEvent.approval_id.in_(approval_ids))
    if attempt_ids:
        conditions.append(AuditEvent.payment_attempt_id.in_(attempt_ids))

    query = select(AuditEvent).where(or_(*conditions)).order_by(
        AuditEvent.created_at.asc(), AuditEvent.event_id.asc()
    )
    all_events = db.execute(query).scalars().all()
    total_count = len(all_events)

    # 5. Apply pagination cursor & limit
    start_idx = 0
    if cursor:
        for idx, ev in enumerate(all_events):
            if ev.event_id == cursor:
                start_idx = idx + 1
                break

    safe_limit = min(max(1, limit), 200)
    paged = all_events[start_idx : start_idx + safe_limit]
    next_cursor = paged[-1].event_id if (start_idx + safe_limit < total_count and paged) else None

    # 6. Format and redact timeline items
    timeline_items: list[TimelineItem] = []
    for ev in paged:
        timeline_items.append(
            TimelineItem(
                event_id=ev.event_id,
                event_type=ev.event_type.value,
                created_at=ev.created_at,
                approval_id=ev.approval_id,
                payment_attempt_id=ev.payment_attempt_id,
                title=format_timeline_title(ev.event_type),
                description=format_timeline_description(ev),
                payload=redact_sensitive(ev.payload or {}),
            )
        )

    return TimelineResponse(
        quote_id=quote_id,
        total_events=total_count,
        events=timeline_items,
        next_cursor=next_cursor,
    )
