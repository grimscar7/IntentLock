"""Authentication dependencies and Supabase JWT verification."""

import hmac

import jwt
from backend.api.config import get_settings
from backend.api.errors import AppError
from fastapi import Header, Request


def get_current_user(
    request: Request,
    authorization: str | None = Header(None),
    x_user_id: str | None = Header(None),
) -> str:
    """Extract and verify authenticated user_id from Supabase JWT or dev fallback."""
    settings = get_settings()

    # 1. Bearer token verification
    if authorization and authorization.startswith("Bearer "):
        token = authorization.split("Bearer ", 1)[1].strip()
        try:
            payload = jwt.decode(
                token,
                settings.SUPABASE_JWT_SECRET,
                algorithms=["HS256"],
                options={"verify_aud": False},
            )
            user_id = payload.get("sub")
            if user_id:
                return str(user_id)
            raise AppError(
                code="UNAUTHORIZED",
                message="Token payload missing user identifier (sub)",
                status_code=401,
            )
        except jwt.PyJWTError as e:
            raise AppError(
                code="UNAUTHORIZED",
                message=f"Invalid or expired authentication token: {e!s}",
                status_code=401,
            )

    # 2. Development / Test fallback via X-User-Id
    is_dev = settings.ENV.lower() in ("development", "dev", "test")
    if is_dev and x_user_id and x_user_id.strip():
        return x_user_id.strip()

    # 3. Missing authentication
    raise AppError(
        code="UNAUTHORIZED",
        message="Authentication required: Provide a valid Bearer token",
        status_code=401,
    )


def require_demo_admin(
    x_demo_admin_token: str | None = Header(None, alias="X-Demo-Admin-Token")
) -> bool:
    """Security guard verifying demo admin token and confirming non-production environment."""
    settings = get_settings()

    # 1. Environment check: demo routes are 404 in production
    env_lower = settings.ENV.lower()
    allowed_envs = [e.lower() for e in settings.DEMO_ALLOWED_ENVS]
    if env_lower in ("production", "prod") or env_lower not in allowed_envs:
        raise AppError(
            code="NOT_FOUND",
            message="Endpoint not found",
            status_code=404,
        )

    # 2. Token comparison with constant-time compare_digest
    if not x_demo_admin_token:
        raise AppError(
            code="FORBIDDEN",
            message="Missing demo admin token",
            status_code=403,
        )

    expected = settings.DEMO_ADMIN_TOKEN
    is_valid = hmac.compare_digest(x_demo_admin_token.encode("utf-8"), expected.encode("utf-8"))
    if not is_valid:
        raise AppError(
            code="FORBIDDEN",
            message="Invalid demo admin token",
            status_code=403,
        )

    return True
