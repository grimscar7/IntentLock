"""ApprovalSnapshot model recording frozen, user-consented purchase terms."""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.api.models.base import (
    Base,
    JSONBType,
    TimestampMixin,
    generate_uuid,
    utc_now,
)
from backend.api.models.enums import ApprovalStatus


class ApprovalSnapshot(Base, TimestampMixin):
    """Immutable snapshot of terms explicitly approved by the buyer. Never mutated on merchant edits."""

    __tablename__ = "approval_snapshots"

    approval_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    quote_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("quotes.quote_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    product_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    product_version_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("product_versions.product_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    total_minor: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        nullable=False,
    )
    policy_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    approved_terms: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
    )
    approved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    status: Mapped[ApprovalStatus] = mapped_column(
        Enum(ApprovalStatus, native_enum=False),
        default=ApprovalStatus.ACTIVE,
        nullable=False,
        index=True,
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_approval_quantity_positive"),
        CheckConstraint("total_minor >= 0", name="check_approval_total_minor_non_negative"),
    )

    quote = relationship("Quote", back_populates="approvals")
    product_version = relationship("ProductVersion", back_populates="approval_snapshots")
    payment_attempts = relationship("PaymentAttempt", back_populates="approval")
