"""Synchronisation avec ma collection sur le site Numista (Phase 4).

- GET  /numista/collection/compare : différences par fiche (N#) entre Obole et Numista.
- POST /numista/collection/push    : ajoute sur Numista les exemplaires qui n'y sont pas encore.
- POST /numista/collection/import  : crée dans Obole un objet pour les exemplaires de Numista absents.

Chaque action porte sur une seule fiche et n'ajoute jamais que la différence : rien n'est supprimé
ni modifié, ni sur Numista ni dans Obole. Chaque appel relit la collection Numista (1 requête +
1 pour le jeton toutes les 2 h) : les décisions se prennent sur l'état réel, pas sur un cache.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlmodel import Session, select

from ..deps import SessionDep, StateDep
from ..models import IdentificationSource, Item, ObjectType
from ..schemas import ItemRead, QuotaRead
from ..services import collection_sync as sync
from ..services.numista import type_url
from .identify import quota_of

router = APIRouter(prefix="/numista/collection", tags=["numista"])


class TypeComparisonRead(BaseModel):
    numista_id: int
    title: str
    url: str
    numista_quantity: int
    obole_quantity: int
    obole_item_ids: list[int]

    @classmethod
    def of(cls, row: sync.TypeComparison) -> "TypeComparisonRead":
        return cls(
            numista_id=row.numista_id, title=row.title, url=type_url(row.numista_id),
            numista_quantity=row.numista_quantity, obole_quantity=row.obole_quantity,
            obole_item_ids=row.obole_item_ids,
        )


class ComparisonRead(BaseModel):
    user_id: int
    only_obole: list[TypeComparisonRead]
    only_numista: list[TypeComparisonRead]
    different: list[TypeComparisonRead]
    same: int
    obole_without_numista: int
    numista_entries: int
    unreadable_entries: int
    quota: QuotaRead


class TypeRequest(BaseModel):
    numista_id: int


class PushResult(BaseModel):
    numista_id: int
    added: int
    grade: str | None
    quota: QuotaRead


def _obole_items(session: Session) -> list[sync.OboleItem]:
    rows = session.exec(
        select(Item.id, Item.numista_id, Item.quantity, Item.numista_title, Item.denomination, Item.condition)
    ).all()
    return [
        sync.OboleItem(id=i, numista_id=n, quantity=q, title=t or d or f"Objet {i}", condition=c)
        for i, n, q, t, d, c in rows
    ]


async def _state_of(state, session: Session) -> tuple[list[dict], sync.Comparison]:
    numista_raw = await state.numista.collected_items()
    return numista_raw, sync.compare(_obole_items(session), numista_raw)


def _row(comparison: sync.Comparison, numista_id: int) -> sync.TypeComparison | None:
    rows = [*comparison.only_obole, *comparison.only_numista, *comparison.different]
    return next((r for r in rows if r.numista_id == numista_id), None)


@router.get("/compare", response_model=ComparisonRead)
async def compare(state: StateDep, session: SessionDep) -> ComparisonRead:
    _, comparison = await _state_of(state, session)
    return ComparisonRead(
        user_id=await state.numista.collection_user_id(),
        only_obole=[TypeComparisonRead.of(r) for r in comparison.only_obole],
        only_numista=[TypeComparisonRead.of(r) for r in comparison.only_numista],
        different=[TypeComparisonRead.of(r) for r in comparison.different],
        same=comparison.same,
        obole_without_numista=comparison.obole_without_numista,
        numista_entries=comparison.numista_entries,
        unreadable_entries=comparison.unreadable_entries,
        quota=quota_of(state),
    )


@router.post("/push", response_model=PushResult)
async def push(body: TypeRequest, state: StateDep, session: SessionDep) -> PushResult:
    """Ajoute sur Numista les exemplaires de cette fiche présents dans Obole mais pas sur Numista."""
    _, comparison = await _state_of(state, session)
    row = _row(comparison, body.numista_id)
    if row is None or row.difference <= 0:
        raise HTTPException(409, "Rien à ajouter sur Numista pour cette fiche : déjà à jour")
    # État transmis seulement s'il est le même pour tous les exemplaires Obole de cette fiche
    grades = {sync.grade_of(i.condition) for i in _obole_items(session) if i.numista_id == body.numista_id}
    grade = grades.pop() if len(grades) == 1 else None
    payload: dict = {"type": body.numista_id, "quantity": row.difference}
    if grade:
        payload["grade"] = grade
    await state.numista.add_collected_item(payload)
    return PushResult(numista_id=body.numista_id, added=row.difference, grade=grade, quota=quota_of(state))


@router.post("/import", response_model=ItemRead, status_code=201)
async def import_type(body: TypeRequest, state: StateDep, session: SessionDep) -> ItemRead:
    """Crée dans Obole un objet pour les exemplaires de cette fiche présents sur Numista seulement."""
    numista_raw, comparison = await _state_of(state, session)
    row = _row(comparison, body.numista_id)
    if row is None or row.difference >= 0:
        raise HTTPException(409, "Rien à importer depuis Numista pour cette fiche : déjà à jour")
    entries = sync.numista_entries_of(numista_raw, body.numista_id)
    first = entries[0]
    years = {e.year for e in entries}
    grades = {e.grade for e in entries}
    grade = grades.pop() if len(grades) == 1 else None
    object_type = sync.CATEGORY_TO_OBJECT_TYPE.get(first.category or "", ObjectType.coin)
    item = Item(
        object_type=ObjectType(object_type),
        country=first.issuer_name,
        issuer_code=first.issuer_code,
        year=years.pop() if len(years) == 1 else None,
        numista_id=body.numista_id,
        numista_title=first.title,
        numista_url=type_url(body.numista_id),
        condition=sync.GRADE_TO_CONDITION.get(grade or ""),
        quantity=-row.difference,
        notes="Importé depuis ma collection Numista",
        identification_source=IdentificationSource.numista,
    )
    session.add(item)
    session.commit()
    session.refresh(item)
    return ItemRead.of(item, [])
