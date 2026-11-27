"""Construction de l'application FastAPI (utilisée par main.py et par les tests)."""

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI

from . import backup, storage
from .config import Settings, get_settings
from .db import init_db, make_engine
from .deps import AppState
from .frontend import mount_frontend
from .routes import export, identify, items, numista, system
from .services.numista import NumistaClient

log = logging.getLogger("obole")


def create_app(
    settings: Settings | None = None,
    *,
    numista_transport: httpx.AsyncBaseTransport | None = None,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Les transports httpx permettent aux tests de simuler Numista et Ollama sans réseau."""
    settings = settings or get_settings()
    data_dir = settings.data_path
    engine = make_engine(data_dir)
    init_db(engine, data_dir)
    state = AppState(
        settings=settings,
        engine=engine,
        numista=NumistaClient(
            settings.numista_api_key.get_secret_value(),
            settings.numista_base_url,
            engine,
            settings.numista_monthly_quota,
            transport=numista_transport,
        ),
        ollama_transport=ollama_transport,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        removed = storage.purge_old_uploads(data_dir)
        if removed:
            log.info("%d envoi(s) de photos expiré(s) supprimé(s)", removed)
        task = None
        if settings.backup_interval_hours > 0:
            task = asyncio.create_task(backup.scheduler(
                data_dir, settings.backup_path, engine,
                settings.backup_interval_hours, settings.backup_keep, state.backup_lock,
            ))
        yield
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        engine.dispose()

    app = FastAPI(title="Obole", version="0.1.0", lifespan=lifespan)
    app.state.obole = state
    for module in (identify, items, numista, export, system):
        app.include_router(module.router, prefix="/api")
    # En dernier : la route « attrape-tout » du frontend ne doit pas masquer l'API
    if not mount_frontend(app, settings.frontend_dist):
        log.warning("Frontend absent (%s) : lancer « npm run build » dans frontend/", settings.frontend_dist)
    return app
