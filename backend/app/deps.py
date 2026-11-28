"""Dépendances FastAPI : tout l'état de l'application vit dans app.state (voir main.create_app)."""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated

import httpx
from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlmodel import Session

from .config import Settings
from .services import matching
from .services.numista import NumistaClient


@dataclass
class AppState:
    settings: Settings
    engine: Engine
    numista: NumistaClient
    ollama_transport: httpx.AsyncBaseTransport | None = None  # injecté par les tests
    web_transport: httpx.AsyncBaseTransport | None = None  # miniatures Numista (tests)
    # Index des émetteurs et ids des catégories Numista, chargés une fois (depuis le cache SQLite)
    _issuer_index: matching.IssuerIndex | None = None
    _type_ids: dict[str, int] | None = None
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    # Une seule sauvegarde automatique à la fois (planifiée ou demandée depuis les réglages)
    backup_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def data_dir(self) -> Path:
        return self.settings.data_path

    @property
    def thumbs_cache_dir(self) -> Path:
        return self.data_dir / "cache" / "thumbs"

    async def matching_context(self) -> tuple[matching.IssuerIndex, dict[str, int]]:
        async with self._lock:
            if self._issuer_index is None:
                self._issuer_index = await matching.load_issuer_index(self.numista)
            if self._type_ids is None:
                self._type_ids = await matching.object_type_ids(self.numista)
            return self._issuer_index, self._type_ids


def get_state(request: Request) -> AppState:
    return request.app.state.obole


def get_session(request: Request) -> Iterator[Session]:
    with Session(get_state(request).engine) as session:
        yield session


StateDep = Annotated[AppState, Depends(get_state)]
SessionDep = Annotated[Session, Depends(get_session)]
