"""Intent model representing user purchase search criteria and requirements."""

from typing import Any

from backend.api.models.base import Base, JSONBType, TimestampMixin, generate_uuid
from backend.api.models.enums import IntentStatus
from sqlalchemy import Enum, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship


class Intent(Base, TimestampMixin):
    """Stores the buyer's natural language request and extracted constraint parameters."""

    __tablename__ = "intents"

    intent_id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=generate_uuid,
    )
    previous_intent_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("intents.intent_id"),
        nullable=True,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(
        String(64),
        index=True,
        nullable=False,
    )
    raw_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    requirements: Mapped[dict[str, Any]] = mapped_column(
        JSONBType,
        nullable=False,
    )
    status: Mapped[IntentStatus] = mapped_column(
        Enum(IntentStatus, native_enum=False),
        default=IntentStatus.PENDING,
        nullable=False,
    )

    quotes = relationship("Quote", back_populates="intent", cascade="all, delete-orphan")
