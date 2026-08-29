from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import AppError
from app.core.logging_setup import RequestIDMiddleware, configure_logging, get_logger
from app.modules.admin.router import router as admin_router
from app.modules.audit.router import router as audit_router
from app.modules.auth.router import router as auth_router
from app.modules.brand_analysis.router import router as brand_analysis_router
from app.modules.brand_profiles.router import router as brand_profiles_router
from app.modules.companies.router import router as companies_router
from app.modules.creatives.router import router as creatives_router
from app.modules.products.router import router as products_router
from app.modules.social_accounts.router import router as social_accounts_router
from app.modules.sub_products.router import router as sub_products_router

configure_logging()
logger = get_logger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="aidigiplanner-backend", version="0.1.0")

    app.add_middleware(RequestIDMiddleware)

    @app.exception_handler(AppError)
    async def handle_app_error(request: Request, exc: AppError) -> JSONResponse:
        logger.info(
            "app_error",
            code=exc.code,
            path=request.url.path,
            status_code=exc.status_code,
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": {"code": exc.code, "message": exc.message}},
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "validation_error",
                    "message": "Invalid request.",
                    "details": jsonable_encoder(exc.errors()),
                }
            },
        )

    @app.exception_handler(Exception)
    async def handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled_error", path=request.url.path)
        return JSONResponse(
            status_code=500,
            content={"error": {"code": "internal_error", "message": "Internal server error."}},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth_router)
    app.include_router(companies_router)
    app.include_router(products_router)
    app.include_router(sub_products_router)
    app.include_router(social_accounts_router)
    app.include_router(brand_profiles_router)
    app.include_router(brand_analysis_router)
    app.include_router(creatives_router)
    app.include_router(admin_router)
    app.include_router(audit_router)

    return app


app = create_app()
