"""Intent creation, extraction, validation, and retrieval services."""

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.api.ai.service import AIClient
from backend.api.config import get_settings
from backend.api.errors import AppError
from backend.api.models.enums import IntentStatus
from backend.api.models.intent import Intent


def validate_intent_requirements(
    extracted: dict[str, Any],
) -> tuple[IntentStatus, dict[str, Any], list[str]]:
    """Validate extracted intent criteria using strict backend code rules."""
    settings = get_settings()
    clarification_questions: list[str] = list(extracted.get("clarification_questions", []))

    category = extracted.get("category")
    max_total_minor = extracted.get("max_total_minor")
    currency = extracted.get("currency")
    minimum_ram_gb = extracted.get("minimum_ram_gb", 0)
    minimum_return_days = extracted.get("minimum_return_days", 0)

    # 1. Category validation
    valid_category = False
    if category and isinstance(category, str):
        cat_lower = category.lower().strip()
        if cat_lower in [c.lower() for c in settings.ALLOWED_CATEGORIES]:
            category = "laptop" if "laptop" in cat_lower else cat_lower
            valid_category = True

    if not valid_category:
        if not any("category" in q.lower() or "type of product" in q.lower() for q in clarification_questions):
            clarification_questions.append(
                f"Please specify a valid product category (e.g., {', '.join(settings.ALLOWED_CATEGORIES)})."
            )

    # 2. Budget validation
    valid_budget = False
    if max_total_minor is not None:
        if isinstance(max_total_minor, (int,)) and not isinstance(max_total_minor, bool):
            if 0 < max_total_minor <= 100_000_000:
                valid_budget = True
            else:
                clarification_questions.append("Budget amount must be a positive reasonable number.")
        else:
            clarification_questions.append("Budget must be specified as an exact integer in minor units.")
    else:
        if not any("budget" in q.lower() for q in clarification_questions):
            clarification_questions.append("Please specify your maximum budget amount.")

    # 3. Currency validation
    valid_currency = False
    if currency and isinstance(currency, str):
        curr_upper = currency.upper().strip()
        if curr_upper in [c.upper() for c in settings.ALLOWED_CURRENCIES]:
            currency = curr_upper
            valid_currency = True

    if not valid_currency:
        if not any("currency" in q.lower() for q in clarification_questions):
            clarification_questions.append(
                f"Please specify a supported currency ({', '.join(settings.ALLOWED_CURRENCIES)})."
            )

    # 4. Sane bounds on RAM & returns
    if not isinstance(minimum_ram_gb, int) or minimum_ram_gb < 0 or minimum_ram_gb > 512:
        minimum_ram_gb = 0
    if not isinstance(minimum_return_days, int) or minimum_return_days < 0 or minimum_return_days > 365:
        minimum_return_days = 0

    requirements = {
        "category": category if valid_category else None,
        "max_total_minor": max_total_minor if valid_budget else None,
        "currency": currency if valid_currency else None,
        "minimum_ram_gb": minimum_ram_gb,
        "minimum_return_days": minimum_return_days,
    }

    if valid_category and valid_budget and valid_currency:
        return IntentStatus.VALIDATED, requirements, []

    return IntentStatus.CLARIFICATION_NEEDED, requirements, clarification_questions


def create_intent(
    user_id: str,
    raw_text: str,
    db: Session,
    ai_client: AIClient,
) -> tuple[Intent, list[str]]:
    """Create, extract, and persist a user intent."""
    # 1. AI extraction
    extracted = ai_client.extract_requirements(raw_text)

    # 2. Code-based validation
    status, requirements, questions = validate_intent_requirements(extracted)

    # 3. Persist
    intent = Intent(
        user_id=user_id,
        raw_text=raw_text,
        requirements=requirements,
        status=status,
    )
    db.add(intent)
    db.commit()
    db.refresh(intent)

    return intent, questions


def get_intent_by_id(user_id: str, intent_id: str, db: Session) -> Intent:
    """Retrieve an intent by ID, enforcing buyer ownership."""
    stmt = select(Intent).where(Intent.intent_id == intent_id)
    intent = db.execute(stmt).scalar_one_or_none()
    if not intent:
        raise AppError("INTENT_NOT_FOUND", f"Intent {intent_id} not found", status_code=404)
    if intent.user_id != user_id:
        raise AppError("FORBIDDEN", "You do not have access to this intent", status_code=403)
    return intent


def update_intent_requirements(
    user_id: str,
    intent_id: str,
    update_data: dict[str, Any],
    db: Session,
) -> Intent:
    """Create a NEW Intent row linked to the previous one with updated requirements."""
    old_intent = get_intent_by_id(user_id=user_id, intent_id=intent_id, db=db)

    # Merge old requirements with updates
    merged = dict(old_intent.requirements or {})
    for k in ("max_total_minor", "minimum_ram_gb", "minimum_return_days", "currency", "category"):
        if update_data.get(k) is not None:
            merged[k] = update_data[k]

    status, validated_reqs, questions = validate_intent_requirements(merged)
    if status != IntentStatus.VALIDATED:
        raise AppError(
            code="VALIDATION_ERROR",
            message="Updated requirements failed validation",
            status_code=400,
            details={"questions": questions},
        )

    raw_text = update_data.get("raw_text") or f"Updated requirements from intent {intent_id}"
    new_intent = Intent(
        user_id=user_id,
        previous_intent_id=intent_id,
        raw_text=raw_text,
        requirements=validated_reqs,
        status=status,
    )
    db.add(new_intent)
    db.commit()
    db.refresh(new_intent)
    return new_intent

