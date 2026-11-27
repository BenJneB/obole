"""Accès direct au catalogue Numista (recherche manuelle, détail d'une fiche), via le cache.

Les erreurs Numista sont converties en réponses HTTP par factory.py (404 / 429 / 502)."""

from typing import Literal

from fastapi import APIRouter, Query

from ..deps import StateDep
from ..schemas import CandidateRead
from ..services.matching import Candidate

router = APIRouter(prefix="/numista", tags=["numista"])


@router.get("/search", response_model=list[CandidateRead])
async def search(
    state: StateDep,
    q: str = Query(min_length=1, description="texte libre, ex. « 2 euro »"),
    issuer: str | None = Query(None, description="code émetteur Numista, ex. france"),
    year: str | None = Query(None, description="année grégorienne ou plage 1800-1850"),
    object_type: Literal["coin", "banknote", "token"] | None = None,
    count: int = Query(10, ge=1, le=50),
    lang: Literal["en", "fr", "es"] = "fr",
) -> list[CandidateRead]:
    """Fiches Numista au même format que les candidats de /identify."""
    type_id = None
    if object_type:
        _, type_ids = await state.matching_context()
        type_id = type_ids.get(object_type)
    data = await state.numista.search_types(
        q, issuer=issuer, date=year, object_type=type_id, count=count, lang=lang
    )
    return [CandidateRead.model_validate(Candidate.from_type(t)) for t in data.get("types") or []]


@router.get("/types/{type_id}")
async def get_type(
    type_id: int, state: StateDep, lang: Literal["en", "fr", "es"] = "fr"
) -> dict:
    return await state.numista.get_type(type_id, lang=lang)
