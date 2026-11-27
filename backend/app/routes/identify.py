"""Identification par photo : modèle vision local puis croisement avec Numista."""

import asyncio
import logging
from typing import Annotated

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

from .. import storage
from ..deps import AppState, StateDep
from ..schemas import IdentifyResponse, MatchRead, QuotaRead
from ..services import matching, vision
from ..services.numista import NumistaError

log = logging.getLogger(__name__)
router = APIRouter(tags=["identification"])


def quota_of(state: AppState) -> QuotaRead:
    client = state.numista
    return QuotaRead(used=client.used_this_month(), limit=client.monthly_quota, remaining=client.remaining())


@router.post("/identify", response_model=IdentifyResponse)
async def identify(
    state: StateDep,
    obverse: Annotated[UploadFile, File(description="photo de l'avers")],
    reverse: Annotated[UploadFile | None, File(description="photo du revers (facultative)")] = None,
) -> IdentifyResponse:
    """Analyse 1 ou 2 photos. Répond toujours 200 avec ce qui a pu être obtenu : si le modèle ou
    Numista échoue, `errors` l'explique et l'objet peut être saisi à la main (photos conservées
    via `upload_id`)."""
    photos = {"obverse": await obverse.read()}
    if reverse is not None and reverse.filename:
        photos["reverse"] = await reverse.read()
    # Vérification Pillow et écriture disque : hors de la boucle asyncio
    upload_id, paths = await asyncio.to_thread(storage.save_upload, state.data_dir, photos)
    await asyncio.to_thread(storage.purge_old_uploads, state.data_dir)

    response = IdentifyResponse(upload_id=upload_id, sides=list(paths), quota=quota_of(state))
    settings = state.settings
    try:
        result = await vision.identify(
            list(paths.values()),
            ollama_url=settings.ollama_url,
            model=settings.vision_model,
            num_ctx=settings.vision_num_ctx,
            transport=state.ollama_transport,
        )
    except vision.VisionError as exc:
        log.warning("Échec du modèle vision : %s", exc)
        response.errors.append(f"Analyse de l'image impossible : {exc}")
        return response
    response.extraction = result.extraction
    response.model = result.model
    response.seconds = round(result.seconds, 1)

    match, errors = await _search(state, result.extraction)
    response.match = match
    response.errors += errors
    response.quota = quota_of(state)
    return response


async def _search(state: AppState, extraction: dict) -> tuple[MatchRead | None, list[str]]:
    try:
        index, type_ids = await state.matching_context()
        match = await matching.find_candidates(state.numista, index, extraction, type_ids)
    except NumistaError as exc:
        log.warning("Échec de la recherche Numista : %s", exc)
        return None, [f"Recherche Numista impossible : {exc}"]
    read = MatchRead.model_validate(match)
    if not match.candidates:
        return read, ["Aucune fiche Numista trouvée : recherche manuelle ou saisie libre."]
    return read, []


class MatchRequest(BaseModel):
    extraction: dict


class MatchResponse(BaseModel):
    match: MatchRead | None
    errors: list[str]
    quota: QuotaRead


@router.post("/match", response_model=MatchResponse)
async def rematch(body: MatchRequest, state: StateDep) -> MatchResponse:
    """Relance la recherche Numista à partir d'une lecture corrigée à la main (sans le modèle)."""
    match, errors = await _search(state, body.extraction)
    return MatchResponse(match=match, errors=errors, quota=quota_of(state))
