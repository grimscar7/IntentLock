"""Quote model representing a merchant price and terms commitment bound to an intent."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.api.models.base import Base, TimestampMixin, generate_uuid
from backend.api.models.enums import QuoteStatus


class Quote(Base, TimestampMixin):
    """Stores a binding quote for a specific product version with a strict time-to-live."""

    __tablename__ = "quotes"

    quote_id: Mapped[str] = mapped_column(
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
    intent_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("intents.intent_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    product_version_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("product_versions.product_version_id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    merchant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    quantity: Mapped[int] = mapped_column(
        Integer,
        default=1,
        nullable=False,
    )
    unit_price_minor: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    shipping_minor: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    tax_minor: Mapped[int] = mapped_column(
        Integer,
        default=0,
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
    status: Mapped[QuoteStatus] = mapped_column(
        Enum(QuoteStatus, native_enum=False),
        default=QuoteStatus.ACTIVE,
        nullable=False,
        index=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="check_quote_quantity_positive"),
        CheckConstraint("unit_price_minor >= 0", name="check_quote_unit_price_minor_non_negative"),
        CheckConstraint("shipping_minor >= 0", name="check_quote_shipping_minor_non_negative"),
        CheckConstraint("tax_minor >= 0", name="check_quote_tax_minor_non_negative"),
        CheckConstraint("total_minor >= 0", name="check_quote_total_minor_non_negative"),
    )

    intent = relationship("Intent", back_populates="quotes")
    product_version = relationship("ProductVersion", back_populates="quotes")
    approvals = relationship("ApprovalSnapshot", back_populates="quote", cascade="all, delete-orphan")
