"""Accès direct au catalogue Numista (recherche manuelle, détail d'une fiche), via le cache."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Query

from ..deps import StateDep
from ..services.numista import NumistaError, NumistaNotFound, QuotaError

router = APIRouter(prefix="/numista", tags=["numista"])


def _http_error(exc: NumistaError) -> HTTPException:
    if isinstance(exc, NumistaNotFound):
        return HTTPException(404, str(exc))
    if isinstance(exc, QuotaError):
        return HTTPException(429, str(exc))
    return HTTPException(502, str(exc))


@router.get("/search")
async def search(
    state: StateDep,
    q: str = Query(min_length=1, description="texte libre, ex. « 2 euro »"),
    issuer: str | None = Query(None, description="code émetteur Numista, ex. france"),
    year: str | None = Query(None, description="année grégorienne ou plage 1800-1850"),
    object_type: Literal["coin", "banknote", "token"] | None = None,
    count: int = Query(10, ge=1, le=50),
    lang: Literal["en", "fr", "es"] = "fr",
) -> dict:
    try:
        type_id = None
        if object_type:
            _, type_ids = await state.matching_context()
            type_id = type_ids.get(object_type)
        return await state.numista.search_types(
            q, issuer=issuer, date=year, object_type=type_id, count=count, lang=lang
        )
    except NumistaError as exc:
        raise _http_error(exc) from exc


@router.get("/types/{type_id}")
async def get_type(
    type_id: int, state: StateDep, lang: Literal["en", "fr", "es"] = "fr"
) -> dict:
    try:
        return await state.numista.get_type(type_id, lang=lang)
    except NumistaError as exc:
        raise _http_error(exc) from exc
