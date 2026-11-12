"""PaymentAttempt model for PayPal checkout integration."""

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.api.models.base import Base, TimestampMixin, generate_uuid
from backend.api.models.enums import PaymentAttemptStatus


class PaymentAttempt(Base, TimestampMixin):
    """Tracks PayPal payment creation, consent binding, and capture execution."""

    __tablename__ = "payment_attempts"

    payment_attempt_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    approval_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("approval_snapshots.approval_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
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
    status: Mapped[PaymentAttemptStatus] = mapped_column(
        Enum(PaymentAttemptStatus, native_enum=False),
        default=PaymentAttemptStatus.INITIATED,
        nullable=False,
        index=True,
    )
    paypal_order_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )
    paypal_capture_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
    )
    last_error_code: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    idempotency_key: Mapped[str] = mapped_column(
        String(128),
        unique=True,
        nullable=False,
        index=True,
    )

    __table_args__ = (
        CheckConstraint("total_minor >= 0", name="check_payment_attempt_total_minor_non_negative"),
    )

    approval = relationship("ApprovalSnapshot", back_populates="payment_attempts")
