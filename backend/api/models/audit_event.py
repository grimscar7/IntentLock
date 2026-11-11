"""AuditEvent model providing an append-only audit trail of consent and lifecycle changes."""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.api.models.base import Base, JSONBType, generate_uuid, utc_now
from backend.api.models.enums import AuditEventType


class AuditEvent(Base):
    """Immutable, append-only record tracking all lifecycle transitions and validations."""

    __tablename__ = "audit_events"

    event_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    quote_id: Mapped[str] = mapped_column(
        String(36),
        nullable=False,
        index=True,
    )
    approval_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    payment_attempt_id: Mapped[str | None] = mapped_column(
        String(36),
        nullable=True,
        index=True,
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(AuditEventType, native_enum=False),
        nullable=False,
        index=True,
    )
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
        index=True,
    )
