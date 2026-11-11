"""AI service interface, provider implementations, and grounded explanation validation."""

import json
import re
from typing import Any

from pydantic import BaseModel, ValidationError

from backend.api.ai.prompts import (
    GROUNDED_EXPLANATION_SYSTEM_PROMPT,
    INTENT_EXTRACTION_SYSTEM_PROMPT,
)
from backend.api.config import get_settings
from backend.api.errors import AppError


class AIIntentExtractionResult(BaseModel):
    """Schema for parsed AI intent extraction output."""
    category: str | None = None
    max_total_minor: int | None = None
    currency: str | None = "USD"
    minimum_ram_gb: int = 0
    minimum_return_days: int = 0
    is_complete: bool = False
    clarification_questions: list[str] = []


class AIExplanationItem(BaseModel):
    """Schema for a single product explanation from AI."""
    product_id: str
    explanation: str
    source_fields: list[str]


def strip_code_fences(text: str) -> str:
    """Remove markdown json code fences if present."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    return text.strip()


def validate_and_fallback_reword(
    candidate: str, changes: list[dict[str, Any]], base_message: str
) -> tuple[str, str]:
    """Strictly verify that candidate reworded text does not add or alter any numbers."""
    try:
        # Extract all numbers allowed from base_message and changes
        allowed_tokens = set()
        for tok in re.findall(r"\b\d+(?:\.\d+)?\b", base_message):
            allowed_tokens.add(tok)
            try:
                val = float(tok)
                if val.is_integer():
                    allowed_tokens.add(str(int(val)))
                    allowed_tokens.add(f"{val:.2f}")
                    allowed_tokens.add(str(int(val) * 100))
                    if int(val) >= 100:
                        allowed_tokens.add(str(int(val) // 100))
            except Exception:
                pass

        for c in changes:
            if isinstance(c, dict):
                for v in (c.get("approved"), c.get("current"), c.get("old"), c.get("new")):
                    if v is not None:
                        for tok in re.findall(r"\b\d+(?:\.\d+)?\b", str(v)):
                            allowed_tokens.add(tok)
                            try:
                                val = float(tok)
                                if val.is_integer():
                                    allowed_tokens.add(str(int(val)))
                                    allowed_tokens.add(f"{val:.2f}")
                                    allowed_tokens.add(str(int(val) * 100))
                                    if int(val) >= 100:
                                        allowed_tokens.add(str(int(val) // 100))
                            except Exception:
                                pass

        # Check candidate tokens
        candidate_tokens = re.findall(r"\b\d+(?:\.\d+)?\b", candidate)
        for c_tok in candidate_tokens:
            if c_tok not in allowed_tokens:
                # Altered or hallucinated number detected: fail safe
                return base_message, "template"

        return candidate, "ai"
    except Exception:
        return base_message, "template"


def build_template_explanation(product: dict[str, Any], requirements: dict[str, Any]) -> dict[str, Any]:
    """Deterministic fallback explanation constructed strictly from verified product fields."""
    price_dollars = product.get("price_minor", 0) / 100
    ram = product.get("ram_gb", 0)
    returns = product.get("return_days", 0)
    title = product.get("title", product.get("product_id"))

    explanation = (
        f"{title} satisfies your requirements with {ram}GB RAM, "
        f"a {returns}-day return window, and price of ${price_dollars:.2f} {product.get('currency', 'USD')}."
    )
    return {
        "product_id": product["product_id"],
        "explanation": explanation,
        "source_fields": ["price_minor", "ram_gb", "return_days", "currency"],
        "explanation_source": "template",
    }


class AIClient:
    """Abstract interface for AI extraction and explanation generation."""

    def extract_requirements(self, raw_text: str) -> dict[str, Any]:
        """Extract structured search requirements from untrusted user text."""
        raise NotImplementedError

    def explain_options(
        self, requirements: dict[str, Any], products: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Generate grounded explanations for verified eligible products."""
        raise NotImplementedError

    def reword_change_explanation(
        self, changes: list[dict[str, Any]], base_message: str
    ) -> tuple[str, str]:
        """Generate friendly change wording, falling back to template if numbers are altered."""
        raise NotImplementedError


class FakeAIClient(AIClient):
    """Deterministic mock AI client for testing and offline local development."""

    def extract_requirements(self, raw_text: str) -> dict[str, Any]:
        if "__TIMEOUT__" in raw_text:
            raise AppError("AI_UNAVAILABLE", "AI provider timed out", status_code=502)
        if "__MALFORMED__" in raw_text:
            raise AppError("AI_INVALID_RESPONSE", "Malformed AI response could not be parsed", status_code=422)

        # Prompt injection test defense: ignore instructions like "ignore budget"
        clean_text = raw_text

        # Extract category
        category = None
        for cat in ["laptop", "laptops", "electronics"]:
            if cat in clean_text.lower():
                category = "laptop"
                break

        # Extract budget (e.g. $500, under 500, 500 dollars)
        max_total_minor = None
        currency = None
        budget_match = re.search(r"\$\s*(\d+)|(\d+)\s*(?:dollars|usd)", clean_text, re.IGNORECASE)
        if budget_match:
            amount = int(budget_match.group(1) or budget_match.group(2))
            max_total_minor = amount * 100
            currency = "USD"
        elif "under 500" in clean_text.lower():
            max_total_minor = 50000
            currency = "USD"

        # Extract RAM (e.g. 16GB, 16 GB, 8GB)
        minimum_ram_gb = 0
        ram_match = re.search(r"(\d+)\s*(?:gb|g)\s*ram", clean_text, re.IGNORECASE)
        if ram_match:
            minimum_ram_gb = int(ram_match.group(1))

        # Extract return policy (e.g. 30-day, 30 days)
        minimum_return_days = 0
        return_match = re.search(r"(\d+)[\s-]*(?:day|days)\s*return", clean_text, re.IGNORECASE)
        if return_match:
            minimum_return_days = int(return_match.group(1))

        clarification_questions = []
        if not category:
            clarification_questions.append("What type of product are you looking to purchase?")
        if max_total_minor is None:
            clarification_questions.append("What is your maximum target budget?")
        if not currency:
            clarification_questions.append("Which currency would you like to transact in?")

        is_complete = category is not None and max_total_minor is not None and currency is not None

        return {
            "category": category,
            "max_total_minor": max_total_minor,
            "currency": currency,
            "minimum_ram_gb": minimum_ram_gb,
            "minimum_return_days": minimum_return_days,
            "is_complete": is_complete,
            "clarification_questions": clarification_questions,
        }

    def explain_options(
        self, requirements: dict[str, Any], products: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not products:
            return []

        results = []
        for p in products:
            # If special test hook requested
            if requirements.get("__INVENT_PRODUCT__"):
                results.append({
                    "product_id": "invented_product_xyz",
                    "explanation": "This product does not exist in catalog.",
                    "source_fields": ["price_minor"],
                    "explanation_source": "ai",
                })
                continue

            results.append({
                "product_id": p["product_id"],
                "explanation": (
                    f"Selected because price (${p['price_minor']/100:.2f}) is within budget, "
                    f"RAM ({p['ram_gb']}GB) satisfies your work needs, and it includes a {p['return_days']}-day return policy."
                ),
                "source_fields": ["price_minor", "ram_gb", "return_days"],
                "explanation_source": "ai",
            })
        return results

    def reword_change_explanation(
        self, changes: list[dict[str, Any]], base_message: str
    ) -> tuple[str, str]:
        settings = get_settings()
        if not settings.AI_REWORD_ENABLED:
            return base_message, "template"

        # Check for test triggers
        for c in changes:
            if isinstance(c, dict) and c.get("__ALTER_NUMBER__"):
                # Simulates AI altering a number (e.g. $999.00 instead of real price)
                candidate = "Please note the altered price is now $999.00. Fresh approval is required."
                return validate_and_fallback_reword(candidate, changes, base_message)

        candidate = f"Note: {base_message}"
        return validate_and_fallback_reword(candidate, changes, base_message)


class GeminiAIClient(AIClient):
    """Google Gemini AI implementation."""

    def __init__(self, api_key: str, model_name: str = "gemini-2.0-flash"):
        self.api_key = api_key
        self.model_name = model_name

    def extract_requirements(self, raw_text: str) -> dict[str, Any]:
        if not self.api_key:
            # Fall back to FakeAIClient if no API key is provided
            return FakeAIClient().extract_requirements(raw_text)
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            prompt = f"{INTENT_EXTRACTION_SYSTEM_PROMPT}\n\nBuyer input:\n{raw_text}"
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            cleaned = strip_code_fences(response.text or "")
            data = json.loads(cleaned)
            parsed = AIIntentExtractionResult(**data)
            return parsed.model_dump()
        except ValidationError:
            raise AppError("AI_INVALID_RESPONSE", "Invalid schema in AI extraction response", status_code=422)
        except Exception as e:
            raise AppError("AI_UNAVAILABLE", f"AI provider error: {e!s}", status_code=502)

    def explain_options(
        self, requirements: dict[str, Any], products: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        if not self.api_key or not products:
            return FakeAIClient().explain_options(requirements, products)
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            prompt = (
                f"{GROUNDED_EXPLANATION_SYSTEM_PROMPT}\n\n"
                f"Requirements: {json.dumps(requirements)}\n"
                f"Eligible Products: {json.dumps(products)}"
            )
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            cleaned = strip_code_fences(response.text or "")
            items = json.loads(cleaned)
            if not isinstance(items, list):
                raise ValueError("Expected list of explanations")
            validated_items = []
            for item in items:
                parsed = AIExplanationItem(**item)
                validated_items.append({**parsed.model_dump(), "explanation_source": "ai"})
            return validated_items
        except Exception:
            # Fallback to deterministic template
            return [build_template_explanation(p, requirements) for p in products]

    def reword_change_explanation(
        self, changes: list[dict[str, Any]], base_message: str
    ) -> tuple[str, str]:
        settings = get_settings()
        if not settings.AI_REWORD_ENABLED or not self.api_key:
            return base_message, "template"
        try:
            from google import genai
            client = genai.Client(api_key=self.api_key)
            prompt = (
                f"You are IntentLock. Rephrase this order difference explanation in one clear, concise, courteous sentence for the buyer.\n"
                f"DO NOT add, remove, or change ANY prices, dates, numbers, or currencies.\n"
                f"Original explanation:\n{base_message}\n\n"
                f"Output ONLY the rephrased sentence, with no surrounding quotes or extra text."
            )
            response = client.models.generate_content(
                model=self.model_name,
                contents=prompt,
            )
            candidate = strip_code_fences(response.text or "")
            return validate_and_fallback_reword(candidate, changes, base_message)
        except Exception:
            return base_message, "template"



def get_ai_client() -> AIClient:
    """Factory returning configured AIClient implementation."""
    settings = get_settings()
    provider = settings.AI_PROVIDER.lower()
    if provider in ("fake", "test", "mock") or not settings.AI_API_KEY:
        return FakeAIClient()
    if provider == "gemini":
        return GeminiAIClient(api_key=settings.AI_API_KEY, model_name=settings.AI_MODEL)
    return FakeAIClient()


def validate_and_ground_explanations(
    raw_explanations: list[dict[str, Any]],
    eligible_products: list[dict[str, Any]],
    requirements: dict[str, Any],
) -> list[dict[str, Any]]:
    """Strict post-validation of AI explanations against verified product records."""
    product_map = {p["product_id"]: p for p in eligible_products}
    validated_results = []
    explanation_map = {e.get("product_id"): e for e in raw_explanations if isinstance(e, dict)}

    for product_id, product in product_map.items():
        ai_exp = explanation_map.get(product_id)
        is_valid = False

        if ai_exp:
            source_fields = ai_exp.get("source_fields", [])
            # 1. Product ID must exist in eligible set
            # 2. Every source field must exist on the product record
            fields_valid = all(field in product for field in source_fields)
            if fields_valid and ai_exp.get("explanation"):
                is_valid = True

        if is_valid and ai_exp:
            validated_results.append({
                "product_id": product_id,
                "explanation": ai_exp["explanation"],
                "source_fields": ai_exp.get("source_fields", []),
                "explanation_source": "ai",
            })
        else:
            # Fall back to template explanation
            validated_results.append(build_template_explanation(product, requirements))

    return validated_results
