"""
Coverage gap tests — pushes the backend test suite to 100% coverage.
"""

import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from backend.api.ai.service import (
    AIClient,
    FakeAIClient,
    GeminiAIClient,
    get_ai_client,
    strip_code_fences,
    validate_and_fallback_reword,
)
from backend.api.audit.service import (
    format_timeline_description,
    get_quote_timeline,
    record_event,
    redact_sensitive,
)
from backend.api.auth import get_current_user
from backend.api.catalog.service import (
    create_product_version,
    evaluate_product_eligibility,
    filter_products,
    get_current_products,
    is_change_material,
    reset_demo_catalog,
)
from backend.api.config import Settings, get_settings
from backend.api.consent.comparator import compare
from backend.api.consent.explain import build_message, format_minor_units
from backend.api.consent.service import (
    consume_approval,
    create_approval,
    validate_before_capture,
    validate_before_create,
)
from backend.api.database import check_db_health, get_db
from backend.api.errors import AppError
from backend.api.intents.service import (
    create_intent,
    get_intent_by_id,
    update_intent_requirements,
    validate_intent_requirements,
)
from backend.api.main import app, create_app
from backend.api.models.approval_snapshot import ApprovalSnapshot
from backend.api.models.audit_event import AuditEvent
from backend.api.models.enums import (
    ApprovalStatus,
    AuditEventType,
    PaymentAttemptStatus,
    QuoteStatus,
    ReasonCode,
)
from backend.api.models.intent import Intent
from backend.api.models.payment_attempt import PaymentAttempt
from backend.api.models.product_version import ProductVersion
from backend.api.models.quote import Quote
from backend.api.quotes.service import create_quote, get_quote_by_id, requote
from backend.api.schemas.demo import ProductUpdateRequest
from backend.api.schemas.validation import Change

HEADERS = {"X-User-Id": "user_cov"}
ADMIN_HEADERS = {"X-Demo-Admin-Token": "demo-admin-secret-token"}


# ===========================================================================
# 1. ai/service.py
# ===========================================================================

class TestStripCodeFences:
    def test_strips_json_fence(self):
        raw = "```json\n{\"key\": 1}\n```"
        assert strip_code_fences(raw) == '{"key": 1}'

    def test_strips_bare_fence(self):
        raw = "```\nhello\n```"
        assert strip_code_fences(raw) == "hello"

    def test_no_fence_passthrough(self):
        assert strip_code_fences("plain text") == "plain text"


class TestValidateAndFallbackReword:
    def test_allows_matching_number(self):
        msg, source = validate_and_fallback_reword("Price is $500.", [], "Price is $500.")
        assert source == "ai"
        assert "500" in msg

    def test_rejects_hallucinated_number(self):
        msg, source = validate_and_fallback_reword("Price is now $999.", [], "Price is $500.")
        assert source == "template"
        assert msg == "Price is $500."

    def test_integer_expansion_ge_100(self):
        msg, source = validate_and_fallback_reword("Total is 500.", [], "Total is 500.")
        assert source == "ai"

    def test_changes_dict_values_added(self):
        changes = [{"approved": 480, "current": 550}]
        msg, source = validate_and_fallback_reword("Changed from 480 to 550.", changes, "Changed from 480 to 550.")
        assert source == "ai"

    def test_overflow_number_exception_branches(self):
        with patch("backend.api.ai.service.float", side_effect=ValueError("boom")):
            msg, source = validate_and_fallback_reword("Price is 500.", [{"approved": 500}], "Price is 500.")
            assert source == "ai"

    def test_exception_path_returns_template(self):
        msg, source = validate_and_fallback_reword("hello", None, "fallback")  # type: ignore[arg-type]
        assert source == "template"
        assert msg == "fallback"


class TestAIClientAbstractMethods:
    def test_extract_requirements_raises(self):
        with pytest.raises(NotImplementedError):
            AIClient().extract_requirements("test")

    def test_explain_options_raises(self):
        with pytest.raises(NotImplementedError):
            AIClient().explain_options({}, [])

    def test_reword_change_explanation_raises(self):
        with pytest.raises(NotImplementedError):
            AIClient().reword_change_explanation([], "msg")


class TestFakeAIClientBranches:
    def test_reword_change_explanation_enabled(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_REWORD_ENABLED=True))
        client = FakeAIClient()
        msg, source = client.reword_change_explanation([], "Price is $500.")
        assert source in ("ai", "template")

    def test_reword_alter_number_trigger(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_REWORD_ENABLED=True))
        client = FakeAIClient()
        changes = [{"__ALTER_NUMBER__": True, "approved": 480, "current": 550}]
        msg, source = client.reword_change_explanation(changes, "Price is $480.")
        assert source == "template"

    def test_extract_requirements_under_500_branch(self):
        client = FakeAIClient()
        reqs = client.extract_requirements("I want a laptop under 500")
        assert reqs["max_total_minor"] == 50000
        assert reqs["currency"] == "USD"

    def test_explain_options_empty_products(self):
        client = FakeAIClient()
        assert client.explain_options({"currency": "USD"}, []) == []


class TestGeminiAIClient:
    def _mock_google(self, response_text: str):
        mock_resp = MagicMock()
        mock_resp.text = response_text
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_resp
        mock_genai = MagicMock()
        mock_genai.Client.return_value = mock_client
        mock_google = MagicMock()
        mock_google.genai = mock_genai
        return mock_google, mock_genai

    def test_extract_no_api_key_fallback(self):
        client = GeminiAIClient(api_key="")
        result = client.extract_requirements("I want a laptop under $500")
        assert isinstance(result, dict)
        assert result["category"] == "laptop"

    def test_explain_no_api_key_fallback(self):
        client = GeminiAIClient(api_key="")
        products = [{"product_id": "p1", "price_minor": 40000, "ram_gb": 8, "return_days": 30}]
        result = client.explain_options({"currency": "USD"}, products)
        assert isinstance(result, list)

    def test_explain_empty_products(self):
        client = GeminiAIClient(api_key="real-key")
        result = client.explain_options({"currency": "USD"}, [])
        assert result == []

    def test_reword_no_api_key_returns_template(self):
        client = GeminiAIClient(api_key="")
        msg, source = client.reword_change_explanation([], "base msg")
        assert source == "template"
        assert msg == "base msg"

    def test_reword_disabled_returns_template(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_REWORD_ENABLED=False))
        client = GeminiAIClient(api_key="some-key")
        msg, source = client.reword_change_explanation([], "base msg")
        assert source == "template"

    def test_extract_valid_response(self, monkeypatch):
        payload = json.dumps({
            "category": "laptop",
            "max_total_minor": 50000,
            "currency": "USD",
            "minimum_ram_gb": 8,
            "minimum_return_days": 0,
            "is_complete": True,
            "clarification_questions": [],
        })
        mock_google, mock_genai = self._mock_google(payload)
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            result = client.extract_requirements("I want a laptop under $500")
        assert result["category"] == "laptop"

    def test_extract_validation_error_raises_app_error(self):
        payload = json.dumps({"minimum_ram_gb": "not_an_int"})
        mock_google, mock_genai = self._mock_google(payload)
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            with pytest.raises(AppError) as exc:
                client.extract_requirements("test")
        assert exc.value.code in ("AI_INVALID_RESPONSE", "AI_UNAVAILABLE")

    def test_extract_network_error_raises_app_error(self):
        mock_genai = MagicMock()
        mock_genai.Client.side_effect = RuntimeError("network error")
        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            with pytest.raises(AppError) as exc:
                client.extract_requirements("test")
        assert exc.value.code == "AI_UNAVAILABLE"

    def test_explain_valid_response(self):
        items = [{"product_id": "p1", "explanation": "Great laptop", "source_fields": ["price_minor"]}]
        mock_google, mock_genai = self._mock_google(json.dumps(items))
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            result = client.explain_options(
                {"currency": "USD"},
                [{"product_id": "p1", "price_minor": 40000, "ram_gb": 8, "return_days": 30}],
            )
        assert len(result) == 1
        assert result[0]["product_id"] == "p1"

    def test_explain_non_list_response_falls_back(self):
        mock_google, mock_genai = self._mock_google(json.dumps({"not": "a list"}))
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            products = [{"product_id": "p1", "price_minor": 40000, "ram_gb": 8, "return_days": 30, "title": "X", "currency": "USD"}]
            result = client.explain_options({"currency": "USD"}, products)
        assert result[0]["explanation_source"] == "template"

    def test_explain_exception_falls_back(self):
        mock_genai = MagicMock()
        mock_genai.Client.side_effect = RuntimeError("boom")
        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            products = [{"product_id": "p1", "price_minor": 40000, "ram_gb": 8, "return_days": 30, "title": "X", "currency": "USD"}]
            result = client.explain_options({"currency": "USD"}, products)
        assert result[0]["explanation_source"] == "template"

    def test_reword_with_enabled_flag(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_REWORD_ENABLED=True, AI_API_KEY="key"))
        mock_google, mock_genai = self._mock_google("Price went up to $550. Fresh approval is required.")
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            msg, source = client.reword_change_explanation(
                [{"approved": 480, "current": 550}],
                "Price went up to $550. Fresh approval is required.",
            )
        assert isinstance(msg, str)

    def test_reword_exception_returns_template(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_REWORD_ENABLED=True, AI_API_KEY="key"))
        mock_genai = MagicMock()
        mock_genai.Client.side_effect = RuntimeError("boom")
        mock_google = MagicMock()
        mock_google.genai = mock_genai
        with patch.dict("sys.modules", {"google": mock_google, "google.genai": mock_genai}):
            client = GeminiAIClient(api_key="key")
            msg, source = client.reword_change_explanation([], "base msg")
        assert source == "template"


class TestGetAIClientFactory:
    def test_returns_fake_when_no_key(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_PROVIDER="gemini", AI_API_KEY=""))
        assert isinstance(get_ai_client(), FakeAIClient)

    def test_returns_gemini_when_key_provided(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_PROVIDER="gemini", AI_API_KEY="real-key"))
        assert isinstance(get_ai_client(), GeminiAIClient)

    def test_returns_fake_for_unknown_provider(self, monkeypatch):
        monkeypatch.setattr("backend.api.ai.service.get_settings", lambda: Settings(AI_PROVIDER="anthropic", AI_API_KEY="key"))
        assert isinstance(get_ai_client(), FakeAIClient)


# ===========================================================================
# 2. audit/service.py
# ===========================================================================

class TestAuditService:
    def test_redact_sensitive_nested(self):
        obj = {"token": "abc", "data": {"password": "secret", "name": "ok"}}
        result = redact_sensitive(obj)
        assert result["token"] == "[REDACTED]"
        assert result["data"]["password"] == "[REDACTED]"
        assert result["data"]["name"] == "ok"

    def test_redact_sensitive_list(self):
        result = redact_sensitive([{"key": "k1"}, "plain"])
        assert result[0]["key"] == "[REDACTED]"
        assert result[1] == "plain"

    def test_redact_sensitive_scalar(self):
        assert redact_sensitive(42) == 42
        assert redact_sensitive("hello") == "hello"

    def test_format_timeline_description_fallback(self):
        event = AuditEvent(quote_id="q1", event_type=AuditEventType.QUOTE_CREATED, payload={})
        dummy = MagicMock()
        dummy.value = "CUSTOM_EVENT"
        event.event_type = dummy
        desc = format_timeline_description(event)
        assert "CUSTOM_EVENT" in desc

    def test_get_quote_timeline_cursor_pagination(self, db_session):
        q = Quote(
            user_id="user_cov",
            intent_id="intent-tl",
            product_version_id="pv-tl",
            merchant_id="merchant_test",
            unit_price_minor=40000,
            shipping_minor=0,
            tax_minor=0,
            total_minor=40000,
            currency="USD",
            quantity=1,
            status=QuoteStatus.ACTIVE,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        db_session.add(q)
        db_session.flush()

        e1 = record_event(db_session, AuditEventType.QUOTE_CREATED, q.quote_id)
        e2 = record_event(db_session, AuditEventType.APPROVAL_GRANTED, q.quote_id)
        db_session.commit()

        result = get_quote_timeline(db_session, "user_cov", q.quote_id, cursor=str(e1.event_id))
        assert result.total_events >= 1

    def test_format_timeline_description_all_branches(self):
        cases = [
            (AuditEventType.QUOTE_CREATED, {"quantity": 2, "currency": "USD", "total_minor": 50000}),
            (AuditEventType.APPROVAL_GRANTED, {"quote_id": "q1", "product_id": "laptop-a"}),
            (AuditEventType.PRODUCT_VERSION_CHANGED, {"product_id": "laptop-a", "version": 2}),
            (AuditEventType.APPROVAL_INVALIDATED, {"reason": "price change"}),
            (AuditEventType.PRE_CREATE_VALIDATION_PASSED, {}),
            (AuditEventType.PRE_CREATE_VALIDATION_FAILED, {"reason_code": "PRICE_CHANGED", "message": "went up"}),
            (AuditEventType.PRE_CAPTURE_VALIDATION_PASSED, {}),
            (AuditEventType.PRE_CAPTURE_VALIDATION_FAILED, {"reason_code": "EXPIRED", "message": "old"}),
        ]
        for et, payload in cases:
            event = AuditEvent(quote_id="q1", event_type=et, payload=payload)
            desc = format_timeline_description(event)
            assert isinstance(desc, str) and len(desc) > 0


# ===========================================================================
# 3. auth.py
# ===========================================================================

class TestAuthJWT:
    def test_valid_bearer_token(self, monkeypatch):
        import jwt as pyjwt
        secret = "a-very-secret-key-that-is-32bytes-long"
        monkeypatch.setattr(get_settings(), "SUPABASE_JWT_SECRET", secret)
        token = pyjwt.encode({"sub": "user_jwt"}, secret, algorithm="HS256")
        from fastapi import Request
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = Request(scope)
        result = get_current_user(request=request, authorization=f"Bearer {token}", x_user_id=None)
        assert result == "user_jwt"

    def test_invalid_bearer_token_raises(self):
        from fastapi import Request
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = Request(scope)
        with pytest.raises(AppError) as exc:
            get_current_user(request=request, authorization="Bearer bad.token.here", x_user_id=None)
        assert exc.value.status_code == 401

    def test_bearer_token_missing_sub_raises(self, monkeypatch):
        import jwt as pyjwt
        secret = "a-very-secret-key-that-is-32bytes-long"
        monkeypatch.setattr(get_settings(), "SUPABASE_JWT_SECRET", secret)
        token = pyjwt.encode({"uid": "no_sub"}, secret, algorithm="HS256")
        from fastapi import Request
        scope = {"type": "http", "method": "GET", "path": "/", "headers": []}
        request = Request(scope)
        with pytest.raises(AppError) as exc:
            get_current_user(request=request, authorization=f"Bearer {token}", x_user_id=None)
        assert exc.value.status_code == 401


# ===========================================================================
# 4. catalog/routes.py
# ===========================================================================

class TestCatalogRoutesIntentNotValidated:
    def test_options_with_unvalidated_intent(self, client, seed_catalog):
        resp = client.post("/api/intents", json={"raw_text": "I need something nice"}, headers=HEADERS)
        assert resp.status_code == 201
        intent_id = resp.json()["intent_id"]

        resp2 = client.get(f"/api/intents/{intent_id}/options", headers=HEADERS)
        assert resp2.status_code == 400
        assert resp2.json()["error"]["code"] == "INTENT_NOT_VALIDATED"


# ===========================================================================
# 5. catalog/service.py
# ===========================================================================

class TestCatalogService:
    def test_is_change_material_branches(self):
        pv = ProductVersion(price_minor=40000, return_days=30, available=True)
        assert is_change_material(pv, 40000, 30, True, policy_bumped=True) is True
        assert is_change_material(pv, 50000, 30, True, False) is True
        assert is_change_material(pv, 40000, 20, True, False) is True
        assert is_change_material(pv, 40000, 30, False, False) is True
        assert is_change_material(pv, 40000, 30, True, False) is False

    def test_evaluate_product_eligibility_missing_fields(self, db_session, seed_catalog):
        p = get_current_products(db_session)[0]
        _, checks, _ = evaluate_product_eligibility(p, {"currency": "USD", "category": "laptop", "minimum_ram_gb": 0, "minimum_return_days": 0})
        assert not checks["within_budget"]

        _, checks, _ = evaluate_product_eligibility(p, {"category": "laptop", "max_total_minor": 100000, "minimum_ram_gb": 0, "minimum_return_days": 0})
        assert not checks["currency_match"]

        orig_ram = p.ram_gb
        p.ram_gb = None
        _, checks, _ = evaluate_product_eligibility(p, {"category": "laptop", "max_total_minor": 100000, "currency": "USD", "minimum_ram_gb": 8, "minimum_return_days": 0})
        assert not checks["ram_satisfaction"]
        p.ram_gb = orig_ram

        orig_ret = p.return_days
        p.return_days = None
        _, checks, _ = evaluate_product_eligibility(p, {"category": "laptop", "max_total_minor": 100000, "currency": "USD", "minimum_ram_gb": 0, "minimum_return_days": 15})
        assert not checks["return_days_satisfaction"]
        p.return_days = orig_ret

        _, checks, _ = evaluate_product_eligibility(p, {"category": "", "max_total_minor": 100000, "currency": "USD", "minimum_ram_gb": 0, "minimum_return_days": 0})
        assert not checks["category_match"]

    def test_evaluate_product_outdated_and_unavailable(self):
        pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-1",
            version=1,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=False,
            is_current=False,
        )
        _, checks, reasons = evaluate_product_eligibility(
            pv, {"currency": "USD", "category": "laptop", "max_total_minor": 50000, "minimum_ram_gb": 0, "minimum_return_days": 0}
        )
        assert "Product version is outdated" in reasons
        assert "Product is not available in inventory" in reasons

    def test_reset_demo_catalog(self, db_session, seed_catalog):
        result = reset_demo_catalog(db_session)
        assert result >= 1

    def test_create_product_version_errors_and_branches(self, db_session, seed_catalog):
        with pytest.raises(AppError) as exc:
            create_product_version(db_session, "laptop_a", {"disallowed_field": 123})
        assert exc.value.code == "VALIDATION_ERROR"

        with pytest.raises(AppError) as exc:
            create_product_version(db_session, "laptop_a", {"price_minor": -10})
        assert exc.value.code == "VALIDATION_ERROR"

        with pytest.raises(AppError) as exc:
            create_product_version(db_session, "laptop_a", {"price_minor": "string_not_int"})
        assert exc.value.code == "VALIDATION_ERROR"

        with pytest.raises(AppError) as exc:
            create_product_version(db_session, "laptop_a", {"available": "not_a_bool"})
        assert exc.value.code == "VALIDATION_ERROR"

        with pytest.raises(AppError) as exc:
            create_product_version(db_session, "nonexistent-product-id", {"price_minor": 50000})
        assert exc.value.code == "PRODUCT_NOT_FOUND"

        # Toggle available branch (line 211)
        res = create_product_version(db_session, "laptop_a", {"available": False})
        assert res["material"] is True

        # Postgres dialect lock branch (line 178)
        with patch.object(db_session.bind.dialect, "name", "postgresql"):
            res2 = create_product_version(db_session, "laptop_a", {"price_minor": 42000})
            assert res2["new_version"] > 1


# ===========================================================================
# 6. config.py
# ===========================================================================

class TestConfigCORSOrigins:
    def test_cors_origins_variants(self):
        s1 = Settings(CORS_ORIGINS="http://localhost:3000,http://localhost:3001")
        assert "http://localhost:3000" in s1.CORS_ORIGINS

        s2 = Settings(CORS_ORIGINS='["http://a.com", "http://b.com"]')
        assert "http://a.com" in s2.CORS_ORIGINS

        s3 = Settings(CORS_ORIGINS="[not-valid-json]")
        assert isinstance(s3.CORS_ORIGINS, list)

        s4 = Settings(CORS_ORIGINS=["http://x.com"])
        assert s4.CORS_ORIGINS == ["http://x.com"]


# ===========================================================================
# 7. consent/comparator.py
# ===========================================================================

class TestConsentComparator:
    def _make_objects(self):
        pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-1",
            version=1,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        quote = Quote(
            quote_id="q1",
            user_id="user_a",
            intent_id="i1",
            product_version_id="pv-1",
            merchant_id="merchant_a",
            quantity=1,
            unit_price_minor=40000,
            shipping_minor=0,
            tax_minor=0,
            total_minor=40000,
            currency="USD",
            status=QuoteStatus.APPROVED,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
        )
        snap = ApprovalSnapshot(
            approval_id="app-1",
            user_id="user_a",
            quote_id="q1",
            product_id="laptop-a",
            product_version_id="pv-1",
            merchant_id="merchant_a",
            quantity=1,
            total_minor=40000,
            currency="USD",
            policy_version=1,
            status=ApprovalStatus.ACTIVE,
            expires_at=datetime.now(UTC) + timedelta(minutes=15),
            approved_terms={"price_minor": 40000, "ram_gb": 16, "return_days": 30},
        )
        reqs = {"max_total_minor": 50000, "minimum_ram_gb": 8, "minimum_return_days": 14, "currency": "USD"}
        return snap, quote, pv, reqs

    def test_compare_ok(self):
        snap, quote, pv, reqs = self._make_objects()
        changes, code = compare(snap, quote, pv, pv, reqs)
        assert code == ReasonCode.OK
        assert len(changes) == 0

    def test_compare_item_mismatch(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-other",
            product_version_id="pv-2",
            version=1,
            merchant_id="merchant_a",
            title="Laptop Other",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.ITEM_MISMATCH

    def test_compare_merchant_mismatch(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_other",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.MERCHANT_MISMATCH

    def test_compare_currency_mismatch(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="EUR",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.CURRENCY_MISMATCH

    def test_compare_quantity_mismatch(self):
        snap, quote, pv, reqs = self._make_objects()
        quote.quantity = 2
        changes, code = compare(snap, quote, pv, pv, reqs)
        assert code == ReasonCode.QUANTITY_MISMATCH

    def test_compare_unavailable(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=False,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.PRODUCT_VERSION_CHANGED

    def test_compare_price_changed(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=55000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.QUOTE_CHANGED

    def test_compare_policy_changed(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=15,
            policy_version=2,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.POLICY_CHANGED

    def test_compare_ram_changed(self):
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=8,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, reqs)
        assert code == ReasonCode.QUOTE_CHANGED

    def test_compare_ram_none_satisfaction(self):
        snap, quote, pv, reqs = self._make_objects()
        snap.approved_terms = {"price_minor": 40000, "ram_gb": None, "return_days": 30}
        curr_pv = ProductVersion(
            product_id="laptop-a",
            product_version_id="pv-2",
            version=2,
            merchant_id="merchant_a",
            title="Laptop A",
            price_minor=40000,
            currency="USD",
            ram_gb=None,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        changes, code = compare(snap, quote, pv, curr_pv, {"minimum_ram_gb": 16, "currency": "USD"})
        assert code == ReasonCode.REQUIREMENTS_NO_LONGER_MET

    def test_compare_detected_codes_fallback_line_191(self):
        import backend.api.consent.comparator as comp_mod
        snap, quote, pv, reqs = self._make_objects()
        curr_pv = ProductVersion(
            product_id="laptop-other",
            product_version_id="pv-2",
            version=1,
            merchant_id="merchant_a",
            title="Laptop Other",
            price_minor=40000,
            currency="USD",
            ram_gb=16,
            return_days=30,
            policy_version=1,
            available=True,
            is_current=True,
        )
        with patch.object(comp_mod, "PRIORITY_ORDER", []):
            changes, code = comp_mod.compare(snap, quote, pv, curr_pv, reqs)
        assert code.value == "ITEM_MISMATCH"


# ===========================================================================
# 8. consent/explain.py
# ===========================================================================

class TestConsentExplain:
    def test_format_minor_units_non_usd(self):
        result = format_minor_units(50000, "EUR")
        assert "EUR" in result
        assert "500" in result

    def test_build_message_all_reason_codes(self):
        codes = [
            ReasonCode.OK,
            ReasonCode.APPROVAL_NOT_FOUND,
            ReasonCode.APPROVAL_EXPIRED,
            ReasonCode.APPROVAL_SUPERSEDED,
            ReasonCode.APPROVAL_INVALIDATED,
            ReasonCode.APPROVAL_ALREADY_CONSUMED,
            ReasonCode.PAYMENT_ATTEMPT_NOT_FOUND,
            ReasonCode.PAYMENT_ATTEMPT_UNBOUND,
            ReasonCode.VALIDATION_ERROR,
        ]
        for c in codes:
            msg = build_message([], c)
            assert len(msg) > 0

    def test_build_message_field_changes(self):
        changes = [
            Change(field="total_minor", approved=40000, current=50000),
            Change(field="price_minor", approved=40000, current=50000),
            Change(field="product_id", approved="p1", current="p2"),
            Change(field="merchant_id", approved="m1", current="m2"),
            Change(field="currency", approved="USD", current="EUR"),
            Change(field="quantity", approved=1, current=2),
            Change(field="return_days", approved=30, current=14),
            Change(field="policy_version", approved=1, current=2),
            Change(field="available", approved=True, current=False),
        ]
        msg = build_message(changes, ReasonCode.QUOTE_CHANGED)
        assert len(msg) > 0

    def test_build_message_only_price_minor(self):
        changes = [Change(field="price_minor", approved=40000, current=50000)]
        msg = build_message(changes, ReasonCode.QUOTE_CHANGED)
        assert "unit price" in msg

    def test_build_message_no_changes_fallbacks(self):
        for code in [
            ReasonCode.ITEM_MISMATCH,
            ReasonCode.MERCHANT_MISMATCH,
            ReasonCode.CURRENCY_MISMATCH,
            ReasonCode.AMOUNT_MISMATCH,
            ReasonCode.POLICY_CHANGED,
            ReasonCode.REQUIREMENTS_NO_LONGER_MET,
            ReasonCode.APPROVAL_NOT_ACTIVE,
        ]:
            msg = build_message([], code)
            assert len(msg) > 0


# ===========================================================================
# 9. consent/service.py
# ===========================================================================

class TestConsentService:
    def _create_full_quote(self, db):
        intent, _ = create_intent("user_cov", "I want a laptop under $700 USD", db, FakeAIClient())
        db.commit()
        products = get_current_products(db)
        prod = products[0]
        quote = create_quote("user_cov", intent.intent_id, prod.product_id, 1, db)
        db.commit()
        return intent, quote

    def test_create_approval_quote_not_found(self, db_session):
        with pytest.raises(AppError) as exc:
            create_approval("user_cov", "nonexistent-quote", db_session)
        assert exc.value.status_code == 404

    def test_create_approval_wrong_user(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        with pytest.raises(AppError) as exc:
            create_approval("wrong_user", quote.quote_id, db_session)
        assert exc.value.status_code == 403

    def test_create_approval_not_active_quote(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        quote.status = QuoteStatus.EXPIRED
        db_session.commit()
        with pytest.raises(AppError) as exc:
            create_approval("user_cov", quote.quote_id, db_session)
        assert exc.value.code == "QUOTE_NOT_ACTIVE"

    def test_create_approval_product_no_longer_current(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        pv = db_session.get(ProductVersion, quote.product_version_id)
        pv.is_current = False
        db_session.commit()
        with pytest.raises(AppError) as exc:
            create_approval("user_cov", quote.quote_id, db_session)
        assert exc.value.code == "QUOTE_NO_LONGER_VALID"

    def test_create_approval_intent_missing(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        quote.intent_id = "nonexistent-intent"
        db_session.commit()
        with pytest.raises(AppError) as exc:
            create_approval("user_cov", quote.quote_id, db_session)
        assert exc.value.code == "QUOTE_NO_LONGER_VALID"

    def test_create_approval_idempotent_on_already_approved(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        a1 = create_approval("user_cov", quote.quote_id, db_session)
        a2 = create_approval("user_cov", quote.quote_id, db_session)
        assert a1.approval_id == a2.approval_id

    def test_create_approval_postgres_dialect_branch(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        with patch.object(db_session.bind.dialect, "name", "postgresql"):
            app_obj = create_approval("user_cov", quote.quote_id, db_session)
            assert app_obj is not None

    def test_validate_before_create_not_found(self, db_session):
        res = validate_before_create(db_session, "user_cov", "nonexistent-approval")
        assert res.allowed is False
        assert res.reason_code == ReasonCode.APPROVAL_NOT_FOUND

    def test_validate_before_create_wrong_user(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        approval = create_approval("user_cov", quote.quote_id, db_session)
        res = validate_before_create(db_session, "wrong_user", approval.approval_id)
        assert res.allowed is False
        assert res.reason_code == ReasonCode.APPROVAL_NOT_FOUND

    def test_consume_approval_not_found(self, db_session):
        assert consume_approval(db_session, "nonexistent-approval") is False

    def test_validate_before_create_success(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        approval = create_approval("user_cov", quote.quote_id, db_session)
        result = validate_before_create(db_session, "user_cov", approval.approval_id)
        assert result.reason_code == ReasonCode.OK

    def test_validate_before_create_approval_expired_and_inactive(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        app1 = create_approval("user_cov", quote.quote_id, db_session)
        app1.status = ApprovalStatus.EXPIRED
        db_session.commit()
        res = validate_before_create(db_session, "user_cov", app1.approval_id)
        assert res.reason_code == ReasonCode.APPROVAL_EXPIRED

        app1.status = ApprovalStatus.SUPERSEDED
        db_session.commit()
        res2 = validate_before_create(db_session, "user_cov", app1.approval_id)
        assert res2.reason_code == ReasonCode.APPROVAL_SUPERSEDED

    def test_validate_before_create_approval_not_active_status(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        app_obj = create_approval("user_cov", quote.quote_id, db_session)
        mock_app = MagicMock(spec=ApprovalSnapshot)
        mock_app.approval_id = app_obj.approval_id
        mock_app.user_id = "user_cov"
        mock_app.quote_id = quote.quote_id
        mock_app.status = "REVOKED"
        with patch.object(db_session, "execute") as mock_exec:
            mock_exec.return_value.scalar_one_or_none.return_value = mock_app
            res = validate_before_create(db_session, "user_cov", app_obj.approval_id)
        assert res.reason_code == ReasonCode.APPROVAL_NOT_ACTIVE

    def test_validate_before_create_quote_status_invalid(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        app_obj = create_approval("user_cov", quote.quote_id, db_session)
        quote.status = QuoteStatus.ACTIVE
        db_session.commit()
        res = validate_before_create(db_session, "user_cov", app_obj.approval_id)
        assert res.reason_code == ReasonCode.QUOTE_INVALID

    def test_validate_before_create_and_capture_audit_record_exception_rollback(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        app_obj = create_approval("user_cov", quote.quote_id, db_session)

        # Pre-create audit commit branch (line 340)
        with patch("backend.api.consent.service.select", side_effect=RuntimeError("unexpected")):
            res = validate_before_create(db_session, "user_cov", "app_id", record_audit=True)
            assert res.reason_code == ReasonCode.VALIDATION_ERROR

        # Pre-create audit rollback branch (lines 341-342)
        with patch("backend.api.consent.service.select", side_effect=RuntimeError("unexpected")):
            with patch.object(db_session, "commit", side_effect=RuntimeError("db commit fail")):
                res = validate_before_create(db_session, "user_cov", "app_id", record_audit=True)
                assert res.reason_code == ReasonCode.VALIDATION_ERROR

        # Pre-capture audit commit branch (line 494)
        attempt = PaymentAttempt(
            user_id="user_cov",
            approval_id=app_obj.approval_id,
            total_minor=app_obj.total_minor,
            currency="USD",
            idempotency_key="idem-key-2",
            status=PaymentAttemptStatus.INITIATED,
        )
        db_session.add(attempt)
        db_session.commit()

        with patch("backend.api.consent.service.select", side_effect=RuntimeError("unexpected")):
            res2 = validate_before_capture(db_session, "user_cov", attempt.payment_attempt_id, record_audit=True)
            assert res2.reason_code == ReasonCode.VALIDATION_ERROR

        # Pre-capture audit rollback branch (lines 495-496)
        with patch("backend.api.consent.service.select", side_effect=RuntimeError("unexpected")):
            with patch.object(db_session, "commit", side_effect=RuntimeError("db commit fail")):
                res3 = validate_before_capture(db_session, "user_cov", attempt.payment_attempt_id, record_audit=True)
                assert res3.reason_code == ReasonCode.VALIDATION_ERROR

    def test_validate_before_capture_currency_mismatch_and_exception(self, db_session, seed_catalog):
        _, quote = self._create_full_quote(db_session)
        app_obj = create_approval("user_cov", quote.quote_id, db_session)

        # Create payment attempt with mismatched currency
        attempt = PaymentAttempt(
            user_id="user_cov",
            approval_id=app_obj.approval_id,
            total_minor=app_obj.total_minor,
            currency="EUR",
            idempotency_key="idem-key-1",
            status=PaymentAttemptStatus.INITIATED,
        )
        db_session.add(attempt)
        db_session.commit()

        res = validate_before_capture(db_session, "user_cov", attempt.payment_attempt_id)
        assert res.reason_code == ReasonCode.CURRENCY_MISMATCH


# ===========================================================================
# 10. database.py
# ===========================================================================

class TestDatabaseModule:
    def test_postgres_url_normalization(self, monkeypatch):
        monkeypatch.setenv("DATABASE_URL", "postgres://user:pw@host/db")
        get_settings.cache_clear()
        import backend.api.database as db_mod
        importlib.reload(db_mod)
        assert db_mod.db_url.startswith("postgresql+psycopg://")

        monkeypatch.setenv("DATABASE_URL", "postgresql://user:pw@host/db")
        get_settings.cache_clear()
        importlib.reload(db_mod)
        assert db_mod.db_url.startswith("postgresql+psycopg://")

        monkeypatch.delenv("DATABASE_URL", raising=False)
        get_settings.cache_clear()
        importlib.reload(db_mod)

    def test_check_db_health_db_none(self):
        result = check_db_health(db=None)
        assert isinstance(result, bool)

    def test_check_db_health_exception_returns_false(self):
        mock_db = MagicMock()
        mock_db.execute.side_effect = Exception("DB error")
        assert check_db_health(mock_db) is False

    def test_get_db_generator(self):
        gen = get_db()
        sess = next(gen)
        assert sess is not None
        try:
            next(gen)
        except StopIteration:
            pass


# ===========================================================================
# 11. intents/routes.py & service.py
# ===========================================================================

class TestIntentRoutes:
    def test_get_intent_by_id(self, client, seed_catalog):
        resp = client.post("/api/intents", json={"raw_text": "laptop under $600 USD"}, headers=HEADERS)
        assert resp.status_code == 201
        intent_id = resp.json()["intent_id"]

        resp2 = client.get(f"/api/intents/{intent_id}", headers=HEADERS)
        assert resp2.status_code == 200
        assert resp2.json()["intent_id"] == intent_id

    def test_get_intent_not_found(self, client):
        resp = client.get("/api/intents/nonexistent-id", headers=HEADERS)
        assert resp.status_code == 404


class TestIntentsService:
    def test_validate_intent_bad_values(self):
        status, reqs, qs = validate_intent_requirements({"category": "invalid_cat"})
        assert any("category" in q.lower() for q in qs)

        status, reqs, qs = validate_intent_requirements({"category": "laptop", "max_total_minor": "bad_type", "currency": "USD"})
        assert any("minor units" in q.lower() for q in qs)

        status, reqs, qs = validate_intent_requirements({"category": "laptop", "max_total_minor": None, "currency": "USD"})
        assert any("budget" in q.lower() for q in qs)

        status, reqs, qs = validate_intent_requirements({"category": "laptop", "max_total_minor": 50000, "currency": "INVALID"})
        assert any("currency" in q.lower() for q in qs)

        status, reqs, qs = validate_intent_requirements({
            "category": "laptop",
            "max_total_minor": 50000,
            "currency": "USD",
            "minimum_ram_gb": -5,
            "minimum_return_days": 999,
        })
        assert reqs["minimum_ram_gb"] == 0
        assert reqs["minimum_return_days"] == 0

    def test_update_intent_validation_failure(self, db_session, seed_catalog):
        intent, _ = create_intent("user_cov", "laptop under $500 USD", db_session, FakeAIClient())
        db_session.commit()
        with pytest.raises(AppError) as exc:
            update_intent_requirements("user_cov", intent.intent_id, {"currency": "INVALID"}, db_session)
        assert exc.value.status_code == 400


# ===========================================================================
# 12. main.py
# ===========================================================================

class TestMain:
    def test_health_check_db_failure(self, client):
        def failing_db():
            mock = MagicMock()
            mock.execute.side_effect = Exception("DB failure")
            yield mock

        app.dependency_overrides[get_db] = failing_db
        try:
            resp = client.get("/health")
            assert resp.status_code == 503
            assert resp.json()["status"] == "error"
        finally:
            app.dependency_overrides.clear()

    def test_cors_as_string_in_create_app(self, monkeypatch):
        s = Settings()
        s.CORS_ORIGINS = "http://localhost:3000"
        monkeypatch.setattr("backend.api.main.get_settings", lambda: s)
        created = create_app()
        assert created is not None


# ===========================================================================
# 13. quotes/routes.py & service.py
# ===========================================================================

class TestQuotesRoutes:
    def test_get_quote_by_id_and_events(self, client, seed_catalog):
        i_res = client.post("/api/intents", json={"raw_text": "laptop under $800 USD"}, headers=HEADERS)
        intent_id = i_res.json()["intent_id"]

        products = client.get("/api/catalog/products").json()
        p_id = products[0]["product_id"]

        q_res = client.post("/api/quotes", json={"intent_id": intent_id, "product_id": p_id, "quantity": 1}, headers=HEADERS)
        assert q_res.status_code == 201
        quote_id = q_res.json()["quote_id"]

        resp = client.get(f"/api/quotes/{quote_id}", headers=HEADERS)
        assert resp.status_code == 200
        assert resp.json()["quote_id"] == quote_id

        e_res = client.get(f"/api/quotes/{quote_id}/events", headers=HEADERS)
        assert e_res.status_code == 200
        assert "events" in e_res.json()

    def test_get_quote_not_found(self, client):
        resp = client.get("/api/quotes/nonexistent-quote", headers=HEADERS)
        assert resp.status_code == 404


class TestQuotesService:
    def test_create_quote_quantity_le_zero(self, db_session):
        with pytest.raises(AppError) as exc:
            create_quote("user_cov", "intent-id", "laptop_a", 0, db_session)
        assert exc.value.code == "INVALID_QUANTITY"

    def test_create_quote_intent_not_validated(self, db_session, seed_catalog):
        intent, _ = create_intent("user_cov", "I need something nice", db_session, FakeAIClient())
        db_session.commit()
        with pytest.raises(AppError) as exc:
            create_quote("user_cov", intent.intent_id, "laptop_a", 1, db_session)
        assert exc.value.code == "INTENT_NOT_VALIDATED"

    def test_create_quote_budget_exceeded(self, db_session, seed_catalog):
        intent, _ = create_intent("user_cov", "laptop under $500 USD", db_session, FakeAIClient())
        db_session.commit()
        with pytest.raises(AppError) as exc:
            create_quote("user_cov", intent.intent_id, "laptop_a", 2, db_session)
        assert exc.value.code == "PRODUCT_NOT_ELIGIBLE"

    def test_get_quote_by_id_expired(self, db_session, seed_catalog):
        intent, _ = create_intent("user_cov", "laptop under $800 USD", db_session, FakeAIClient())
        db_session.commit()
        quote = create_quote("user_cov", intent.intent_id, "laptop_a", 1, db_session)
        quote.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db_session.commit()

        q, is_expired = get_quote_by_id("user_cov", quote.quote_id, db_session)
        assert is_expired is True

    def test_requote_errors_and_branches(self, db_session, seed_catalog):
        with pytest.raises(AppError) as exc:
            requote(db_session, "user_cov", "nonexistent-quote")
        assert exc.value.status_code == 404

        intent, _ = create_intent("user_cov", "laptop under $800 USD", db_session, FakeAIClient())
        db_session.commit()
        quote = create_quote("user_cov", intent.intent_id, "laptop_a", 1, db_session)
        db_session.commit()

        with pytest.raises(AppError) as exc:
            requote(db_session, "wrong_user", quote.quote_id)
        assert exc.value.status_code == 403

        # Old pv not found
        orig_pvid = quote.product_version_id
        quote.product_version_id = "nonexistent-pvid"
        db_session.commit()
        with pytest.raises(AppError) as exc:
            requote(db_session, "user_cov", quote.quote_id)
        assert exc.value.status_code == 404

        # Restore pv and test unavailable
        quote.product_version_id = orig_pvid
        pv = db_session.get(ProductVersion, orig_pvid)
        pv.available = False
        db_session.commit()
        with pytest.raises(AppError) as exc:
            requote(db_session, "user_cov", quote.quote_id)
        assert exc.value.code == "PRODUCT_NOT_AVAILABLE"

        # Update return days via create_product_version to produce v2 with changed policy (lines 198, 200)
        pv.available = True
        db_session.commit()
        intent2, _ = create_intent("user_cov", "laptop under $800 USD", db_session, FakeAIClient())
        db_session.commit()
        quote2 = create_quote("user_cov", intent2.intent_id, "laptop_b", 1, db_session)
        db_session.commit()
        create_product_version(db_session, "laptop_b", {"return_days": 7})
        db_session.commit()
        new_q, comparison = requote(db_session, "user_cov", quote2.quote_id)
        assert new_q is not None
        assert comparison is not None


# ===========================================================================
# 14. schemas/demo.py
# ===========================================================================

class TestSchemasDemo:
    def test_product_update_request_validations(self):
        with pytest.raises(Exception):
            ProductUpdateRequest(price_minor=-1)

        with pytest.raises(Exception):
            ProductUpdateRequest(price_minor=True)

        with pytest.raises(Exception):
            ProductUpdateRequest(return_days=-5)

        with pytest.raises(Exception):
            ProductUpdateRequest(return_days=True)

        req = ProductUpdateRequest(price_minor=50000, return_days=30)
        assert req.price_minor == 50000


# ===========================================================================
# 15. tests/conftest.py fixture coverage
# ===========================================================================

class TestConftestFixtureCoverage:
    def test_conftest_fixtures(self, fake_ai, controllable_clock):
        assert fake_ai is not None
        controllable_clock.advance(days=1)
        now_skew = controllable_clock.now()
        assert now_skew is not None

    def test_conftest_db_guard_branch(self, monkeypatch):
        import backend.api.tests.conftest as conf_mod
        monkeypatch.setenv("DATABASE_URL", "postgresql://prod_db")
        monkeypatch.setenv("TEST_DATABASE_URL", "postgresql://prod_db")
        with pytest.raises(RuntimeError) as exc:
            importlib.reload(conf_mod)
        assert "CRITICAL SAFETY VIOLATION" in str(exc.value)

        # Restore conftest back
        monkeypatch.delenv("DATABASE_URL", raising=False)
        monkeypatch.setenv("TEST_DATABASE_URL", "sqlite:///:memory:")
        importlib.reload(conf_mod)
