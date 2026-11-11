"""ProductVersion model capturing immutable versioned snapshots of catalog products."""

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.api.models.base import Base, TimestampMixin, generate_uuid


class ProductVersion(Base, TimestampMixin):
    """Immutable version of a product. Catalog edits create a new row with incremented version."""

    __tablename__ = "product_versions"

    product_version_id: Mapped[str] = mapped_column(
        String(64),
        primary_key=True,
        default=generate_uuid,
    )
    product_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    merchant_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    price_minor: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    currency: Mapped[str] = mapped_column(
        String(3),
        default="USD",
        nullable=False,
    )
    ram_gb: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    return_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    policy_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    available: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    is_current: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_product_version"),
        Index("ix_product_id_is_current", "product_id", "is_current"),
        CheckConstraint("price_minor >= 0", name="check_product_price_minor_non_negative"),
    )

    quotes = relationship("Quote", back_populates="product_version")
    approval_snapshots = relationship("ApprovalSnapshot", back_populates="product_version")
