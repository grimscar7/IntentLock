"""FastAPI application factory, global exception handling, and route registration."""

from fastapi import Depends, FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from backend.api.catalog.routes import router as catalog_router
from backend.api.config import get_settings
from backend.api.consent.routes import router as consent_router
from backend.api.database import check_db_health, get_db
from backend.api.errors import AppError, app_error_handler
from backend.api.intents.routes import router as intents_router
from backend.api.quotes.routes import router as quotes_router


def create_app() -> FastAPI:
    """Create and configure the FastAPI application instance."""
    settings = get_settings()

    app = FastAPI(
        title="IntentLock API",
        description="AI explains, backend code enforces consent, PayPal completes approved payments.",
        version="0.2.0",
    )

    # 1. CORS Middleware
    cors_origins = settings.CORS_ORIGINS
    if isinstance(cors_origins, str):
        cors_origins = [cors_origins]

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 2. Global Exception Handlers
    app.add_exception_handler(AppError, app_error_handler)  # type: ignore[arg-type]

    # 3. Router Registration
    app.include_router(intents_router)
    app.include_router(catalog_router)
    app.include_router(quotes_router)
    app.include_router(consent_router)

    # 4. System Health Check
    @app.get("/health", tags=["System"])
    def health_check(db: Session = Depends(get_db)):
        """Health check verifying FastAPI runtime and database connectivity."""
        is_db_ok = check_db_health(db)
        if not is_db_ok:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={
                    "status": "error",
                    "database": "error",
                    "environment": settings.ENV,
                },
            )
        return {
            "status": "ok",
            "database": "ok",
            "environment": settings.ENV,
        }

    return app


app = create_app()
