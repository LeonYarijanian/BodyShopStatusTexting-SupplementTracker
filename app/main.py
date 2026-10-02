"""FastAPI app, route registration and scheduler start."""

import logging
import sys
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException
from starlette.middleware.sessions import SessionMiddleware

from app.auth import SESSION_IDLE_LIMIT, LoginRequired, csrf_protect
from app.config import Settings, get_settings
from app.db import make_engine, make_session_factory
from app.routes import TEMPLATES_DIR, is_htmx, templates
from app.routes import auth_routes, board, messages_routes, repair_orders, reports_routes, settings_routes, supplements_routes

STATIC_DIR = TEMPLATES_DIR.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    problem = settings.secret_key_problem()
    if problem:
        print(f"ERROR: {problem}", file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    engine = make_engine(settings.DATABASE_URL)
    session_factory = make_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        scheduler = None
        if settings.SCHEDULER_ENABLED:
            from app.scheduler import start_scheduler

            scheduler = start_scheduler(app)
        yield
        if scheduler is not None:
            scheduler.shutdown(wait=False)

    app = FastAPI(lifespan=lifespan, dependencies=[Depends(csrf_protect)], docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.engine = engine
    app.state.SessionLocal = session_factory

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.APP_SECRET_KEY,
        max_age=int(SESSION_IDLE_LIMIT.total_seconds()),
        same_site="lax",
        https_only=settings.cookies_secure,
    )
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    for module in (auth_routes, board, repair_orders, messages_routes, supplements_routes, reports_routes, settings_routes):
        app.include_router(module.router)

    @app.exception_handler(LoginRequired)
    async def _login_required(request: Request, exc: LoginRequired):
        if is_htmx(request):
            return HTMLResponse("", status_code=401, headers={"HX-Redirect": "/login"})
        return RedirectResponse("/login", status_code=302)

    @app.exception_handler(HTTPException)
    async def _http_error(request: Request, exc: HTTPException):
        return templates.TemplateResponse(
            request,
            "error.html",
            {"request": request, "status_code": exc.status_code, "detail": exc.detail},
            status_code=exc.status_code,
            headers=getattr(exc, "headers", None),
        )

    return app


app = create_app()
