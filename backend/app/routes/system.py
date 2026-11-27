"""État du serveur et statistiques."""

import httpx
from fastapi import APIRouter
from sqlalchemy import func
from sqlmodel import col, select

from ..db import MIGRATIONS
from ..deps import SessionDep, StateDep
from ..models import Item
from ..schemas import Stats
from ..services import vision
from .identify import quota_of
from .items import list_tags

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
    count, quantity, countries, value, spent, identified = session.exec(
        select(
            func.count(Item.id),
            func.coalesce(func.sum(Item.quantity), 0),
            # "" exclu, comme dans by_country
            func.count(func.distinct(func.nullif(func.lower(Item.country), ""))),
            func.coalesce(func.sum(Item.estimated_value * Item.quantity), 0.0),
            func.coalesce(func.sum(Item.acquired_price), 0.0),
            func.count(Item.numista_id),
        )
    ).one()
    by_type = dict(
        session.exec(select(Item.object_type, func.count(Item.id)).group_by(Item.object_type)).all()
    )
    by_country = session.exec(
        select(Item.country, func.count(Item.id))
        .where(col(Item.country).is_not(None), Item.country != "")
        .group_by(func.lower(Item.country))
        .order_by(func.count(Item.id).desc(), func.lower(Item.country))
    ).all()
    by_year = dict(session.exec(
        select(Item.year, func.count(Item.id))
        .where(col(Item.year).is_not(None)).group_by(Item.year).order_by(Item.year)
    ).all())
    by_condition = dict(session.exec(
        select(func.coalesce(Item.condition, ""), func.count(Item.id))
        .group_by(func.coalesce(Item.condition, ""))
    ).all())
    by_tag = {t.tag: t.count for t in list_tags(session)}
    return Stats(
        items=count, total_quantity=quantity, countries=countries,
        by_type={str(k): v for k, v in by_type.items()}, by_country=dict(by_country),
        by_year=by_year, without_year=count - sum(by_year.values()),
        by_condition=by_condition, by_tag=by_tag, identified=identified,
        estimated_value_total=round(value, 2), acquired_price_total=round(spent, 2),
    )
