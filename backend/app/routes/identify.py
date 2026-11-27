"""Identification par photo : modèle vision local puis croisement avec Numista."""

import logging
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import storage
from ..deps import AppState, StateDep
from ..schemas import CandidateRead, IdentifyResponse, MatchRead, QuotaRead
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
    try:
        upload_id, paths = storage.save_upload(state.data_dir, photos)
    except storage.PhotoError as exc:
        raise HTTPException(400, str(exc)) from exc
    storage.purge_old_uploads(state.data_dir)

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

    try:
        index, type_ids = await state.matching_context()
        match = await matching.find_candidates(state.numista, index, result.extraction, type_ids)
    except NumistaError as exc:
        log.warning("Échec de la recherche Numista : %s", exc)
        response.errors.append(f"Recherche Numista impossible : {exc}")
    else:
        response.match = MatchRead(
            issuer_code=match.issuer_code,
            issuer_name=match.issuer_name,
            issuer_score=match.issuer_score,
            queries=match.queries,
            candidates=[
                CandidateRead(
                    id=c.id, title=c.title, issuer=c.issuer, years=c.years, url=c.url,
                    obverse_thumbnail=c.obverse_thumbnail, reverse_thumbnail=c.reverse_thumbnail,
                )
                for c in match.candidates
            ],
        )
        if not match.candidates:
            response.errors.append("Aucune fiche Numista trouvée : recherche manuelle ou saisie libre.")
    response.quota = quota_of(state)
    return response
