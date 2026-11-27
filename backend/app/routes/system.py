"""État du serveur et statistiques."""

import httpx
from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import select

from ..db import MIGRATIONS
from ..deps import SessionDep, StateDep
from ..models import Item
from ..schemas import Stats
from ..services import vision
from .identify import quota_of

router = APIRouter(tags=["système"])


@router.get("/health")
async def health(state: StateDep) -> dict:
    """N'interroge jamais Numista (économie de quota) : seule la configuration est vérifiée."""
    settings = state.settings
    ollama: dict = {"url": settings.ollama_url, "model": settings.vision_model, "ok": False}
    try:
        async with httpx.AsyncClient(timeout=3, transport=state.ollama_transport) as client:
            response = await client.get(f"{settings.ollama_url.rstrip('/')}/api/version")
        response.raise_for_status()
        ollama["version"] = response.json().get("version")
        capabilities = await vision.model_capabilities(
            settings.ollama_url, settings.vision_model, transport=state.ollama_transport
        )
        ollama["ok"] = "vision" in capabilities
        if not ollama["ok"]:
            ollama["error"] = f"Le modèle {settings.vision_model} ne lit pas les images"
    except (httpx.HTTPError, ValueError, vision.VisionError) as exc:
        ollama["error"] = str(exc) or type(exc).__name__

    numista = {"configured": state.numista.configured, **quota_of(state).model_dump()}
    return {
        "status": "ok" if ollama["ok"] and numista["configured"] else "degraded",
        "ollama": ollama,
        "numista": numista,
        "schema_version": len(MIGRATIONS),
    }


@router.get("/stats", response_model=Stats)
def stats(session: SessionDep) -> Stats:
    count, quantity, countries, value = session.exec(
        select(
            func.count(Item.id),
            func.coalesce(func.sum(Item.quantity), 0),
            func.count(func.distinct(func.lower(Item.country))),
            func.coalesce(func.sum(Item.estimated_value * Item.quantity), 0.0),
        )
    ).one()
    by_type = dict(
        session.exec(select(Item.object_type, func.count(Item.id)).group_by(Item.object_type)).all()
    )
    return Stats(
        items=count, total_quantity=quantity, countries=countries,
        by_type={str(k): v for k, v in by_type.items()}, estimated_value_total=round(value, 2),
    )
