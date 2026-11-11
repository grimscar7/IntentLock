"""API endpoints for user intent registration and retrieval."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.api.ai.service import get_ai_client
from backend.api.auth import get_current_user
from backend.api.database import get_db
from backend.api.intents.service import (
    create_intent,
    get_intent_by_id,
    update_intent_requirements,
)
from backend.api.schemas import (
    IntentCreateRequest,
    IntentRequirementsUpdateRequest,
    IntentResponse,
)

router = APIRouter(prefix="/api/intents", tags=["Intents"])


@router.post("", response_model=IntentResponse, status_code=201)
def submit_intent(
    payload: IntentCreateRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Submit a natural language shopping request for requirement extraction."""
    ai_client = get_ai_client()
    intent, questions = create_intent(
        user_id=user_id,
        raw_text=payload.raw_text,
        db=db,
        ai_client=ai_client,
    )
    return IntentResponse(
        intent_id=intent.intent_id,
        user_id=intent.user_id,
        status=intent.status.value,
        raw_text=intent.raw_text,
        requirements=intent.requirements,
        clarification_questions=questions if questions else None,
        created_at=intent.created_at,
    )


@router.get("/{intent_id}", response_model=IntentResponse)
def get_intent(
    intent_id: str,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Retrieve an intent by ID with ownership enforcement."""
    intent = get_intent_by_id(user_id=user_id, intent_id=intent_id, db=db)
    return IntentResponse(
        intent_id=intent.intent_id,
        user_id=intent.user_id,
        status=intent.status.value,
        raw_text=intent.raw_text,
        requirements=intent.requirements,
        clarification_questions=None,
        previous_intent_id=intent.previous_intent_id,
        created_at=intent.created_at,
    )


@router.post("/{intent_id}/requirements", response_model=IntentResponse, status_code=201)
def update_requirements(
    intent_id: str,
    payload: IntentRequirementsUpdateRequest,
    user_id: str = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Update requirements on an intent by creating a new chained intent."""
    update_data = payload.model_dump(exclude_unset=True)
    new_intent = update_intent_requirements(
        user_id=user_id,
        intent_id=intent_id,
        update_data=update_data,
        db=db,
    )
    return IntentResponse(
        intent_id=new_intent.intent_id,
        user_id=new_intent.user_id,
        status=new_intent.status.value,
        raw_text=new_intent.raw_text,
        requirements=new_intent.requirements,
        clarification_questions=None,
        previous_intent_id=new_intent.previous_intent_id,
        created_at=new_intent.created_at,
    )
