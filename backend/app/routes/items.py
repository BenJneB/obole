"""Collection : création, liste filtrée, lecture, modification, suppression, photos."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, delete, func, literal, or_
from sqlmodel import Session, col, select

from .. import storage
from ..deps import AppState, SessionDep, StateDep
from ..models import TAG_SEPARATOR, Item, ItemFields, Photo, PhotoSide, normalize_tags, split_tags, utcnow
from ..schemas import ItemCreate, ItemList, ItemRead, PhotoRead, thumb_url
from ..services.numista import type_url

router = APIRouter(tags=["collection"])

SORTABLE = {"created_at", "updated_at", "year", "country", "denomination", "estimated_value", "quantity"}
LIKE_ESCAPE = "\\"
SEARCHED = ("country", "denomination", "currency", "numista_title", "notes", "tags", "storage_location")


def has_tag(tag: str) -> ColumnElement[bool]:
    """Vrai si l'objet porte ce tag (tags normalisés « a, b, c », comparaison sans casse)."""
    wrapped = literal(TAG_SEPARATOR) + func.lower(col(Item.tags)) + literal(TAG_SEPARATOR)
    # instr plutôt que LIKE : un tag peut contenir % ou _ (lower() est celui de Python, voir db.py)
    return func.instr(wrapped, f"{TAG_SEPARATOR}{tag.lower()}{TAG_SEPARATOR}") > 0


def _contains(text: str) -> str:
    """Motif LIKE « contient » : % et _ saisis sont cherchés tels quels."""
    escaped = text.strip().replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", r"\%").replace("_", r"\_")
    return f"%{escaped}%"


def photos_by_item(session: Session, item_ids: list[int] | None = None) -> dict[int, list[Photo]]:
    """Photos regroupées par objet (ordre d'ajout) ; toutes les photos si item_ids est None."""
    query = select(Photo).order_by(col(Photo.id))
    if item_ids is not None:
        if not item_ids:
            return {}
        query = query.where(col(Photo.item_id).in_(item_ids))
    grouped: dict[int, list[Photo]] = {item_id: [] for item_id in item_ids or []}
    for photo in session.exec(query):
        grouped.setdefault(photo.item_id, []).append(photo)
    return grouped


def _photos(session: Session, item_id: int) -> list[Photo]:
    return photos_by_item(session, [item_id])[item_id]


def _get_item(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"Objet {item_id} introuvable")
    return item


def _fill_numista_url(item: Item) -> None:
    """L'URL Numista se déduit toujours du N# (jamais saisie à la main)."""
    item.numista_url = type_url(item.numista_id) if item.numista_id else None


@router.post("/items", response_model=ItemRead, status_code=201)
def create_item(
    body: ItemCreate, session: SessionDep, state: StateDep
) -> ItemRead:
    uploads = {}
    if body.upload_id:
        uploads = storage.upload_files(state.data_dir, body.upload_id)

    item = Item(**body.model_dump(exclude={"upload_id", "ai_extraction"}))
    if body.ai_extraction is not None:
        item.ai_raw_json = json.dumps(body.ai_extraction, ensure_ascii=False)
    _fill_numista_url(item)
    session.add(item)
    session.flush()  # attribue item.id

    stored: list[str] = []
    try:
        for side, path in uploads.items():
            photo_path, thumb_path = storage.store_item_photo(state.data_dir, item.id, side, path)
            stored += [photo_path, thumb_path]
            session.add(Photo(item_id=item.id, side=PhotoSide(side), path=photo_path, thumb_path=thumb_path))
        session.commit()
    except Exception:
        session.rollback()
        storage.delete_files(state.data_dir, *stored)
        raise
    if body.upload_id:
        storage.discard_upload(state.data_dir, body.upload_id)
    session.refresh(item)
    return ItemRead.of(item, _photos(session, item.id))


@router.get("/items", response_model=ItemList)
def list_items(
    session: SessionDep,
    q: str | None = Query(None, description="recherche texte (pays, valeur, titre, notes, tags...)"),
    country: str | None = None,
    object_type: Literal["coin", "banknote", "token", "unknown"] | None = None,
    year: int | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    numista_id: int | None = None,
    tag: str | None = Query(None, description="tag exact (sans tenir compte de la casse)"),
    condition: str | None = Query(None, description="état exact (UNC, SUP...) ; « none » = non précisé"),
    storage: str | None = Query(None, description="emplacement (contient)"),
    identified: bool | None = Query(None, description="true = avec fiche Numista, false = sans"),
    sort: str = Query("-created_at", description="champ de tri, préfixe « - » pour décroissant"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ItemList:
    conditions = []
    if q:
        pattern = _contains(q)
        conditions.append(or_(*(
            col(getattr(Item, name)).ilike(pattern, escape=LIKE_ESCAPE) for name in SEARCHED
        )))
    if country:
        conditions.append(func.lower(Item.country) == func.lower(country.strip()))
    if object_type:
        conditions.append(Item.object_type == object_type)
    if year is not None:
        conditions.append(Item.year == year)
    if year_min is not None:
        conditions.append(col(Item.year) >= year_min)
    if year_max is not None:
        conditions.append(col(Item.year) <= year_max)
    if numista_id is not None:
        conditions.append(Item.numista_id == numista_id)
    if tag and (normalized := normalize_tags(tag)):
        conditions.append(has_tag(normalized))
    if condition == "none":
        conditions.append(or_(col(Item.condition).is_(None), Item.condition == ""))
    elif condition:
        conditions.append(Item.condition == condition)
    if storage:
        conditions.append(col(Item.storage_location).ilike(_contains(storage), escape=LIKE_ESCAPE))
    if identified is not None:
        conditions.append(col(Item.numista_id).is_not(None) if identified else col(Item.numista_id).is_(None))

    field = sort.lstrip("-")
    if field not in SORTABLE:
        possible = ", ".join(sorted(SORTABLE))
        raise HTTPException(422, f"Tri impossible sur « {field} » (possibles : {possible})")
    order = col(getattr(Item, field))
    order = order.desc() if sort.startswith("-") else order.asc()

    total = session.exec(select(func.count()).select_from(Item).where(*conditions)).one()
    items = list(session.exec(
        select(Item).where(*conditions).order_by(order.nulls_last(), col(Item.id).desc())
        .offset(offset).limit(limit)
    ))
    photos = photos_by_item(session, [item.id for item in items])
    return ItemList(total=total, items=[ItemRead.of(item, photos[item.id]) for item in items])


class TagCount(BaseModel):
    tag: str
    count: int


@router.get("/tags", response_model=list[TagCount])
def list_tags(session: SessionDep) -> list[TagCount]:
    """Tous les tags utilisés, du plus fréquent au moins fréquent (suggestions et filtre)."""
    counts: dict[str, list] = {}  # minuscule -> [graphie la plus récente, nombre]
    for tags in session.exec(select(Item.tags).where(col(Item.tags).is_not(None)).order_by(col(Item.id))):
        for tag in split_tags(tags):
            entry = counts.setdefault(tag.lower(), [tag, 0])
            entry[0] = tag
            entry[1] += 1
    ranked = sorted(counts.values(), key=lambda e: (-e[1], e[0].lower()))
    return [TagCount(tag=tag, count=count) for tag, count in ranked]


class OwnedItem(BaseModel):
    id: int
    year: int | None
    mint_mark: str | None
    condition: str | None
    quantity: int
    denomination: str | None
    country: str | None
    thumb_url: str | None


@router.get("/items/owned", response_model=dict[int, list[OwnedItem]])
def owned(
    session: SessionDep,
    numista_ids: str = Query(..., description="N# séparés par des virgules (max. 100)"),
) -> dict[int, list[OwnedItem]]:
    """Objets déjà dans la collection pour ces fiches Numista (détection des doublons).
    Une fiche couvre souvent plusieurs années : c'est à moi de juger si c'est le même objet."""
    try:
        ids = {int(part) for part in numista_ids.split(",") if part.strip()}
    except ValueError as exc:
        raise HTTPException(422, "numista_ids : liste de nombres attendue") from exc
    if not ids:
        return {}
    if len(ids) > 100:
        raise HTTPException(422, "100 N# au maximum")
    items = list(session.exec(
        select(Item).where(col(Item.numista_id).in_(ids)).order_by(col(Item.year), col(Item.id))
    ))
    photos = photos_by_item(session, [item.id for item in items])
    result: dict[int, list[OwnedItem]] = {}
    for item in items:
        result.setdefault(item.numista_id, []).append(OwnedItem(
            **item.model_dump(include=set(OwnedItem.model_fields) - {"thumb_url"}),
            thumb_url=thumb_url(photos[item.id]),
        ))
    return result


class IncrementBody(BaseModel):
    by: int = Field(1, ge=1, le=1000)
    # Photos d'une identification devenues inutiles (doublon) : supprimées tout de suite
    discard_upload_id: str | None = None


@router.post("/items/{item_id}/increment", response_model=ItemRead)
def increment(item_id: int, body: IncrementBody, session: SessionDep, state: StateDep) -> ItemRead:
    """Doublon : un exemplaire de plus d'un objet existant, au lieu d'un nouvel objet."""
    item = _get_item(session, item_id)
    item.quantity += body.by
    item.updated_at = utcnow()
    session.add(item)
    session.commit()
    if body.discard_upload_id:
        storage.discard_upload(state.data_dir, body.discard_upload_id)
    session.refresh(item)
    return ItemRead.of(item, _photos(session, item_id))


@router.get("/items/{item_id}", response_model=ItemRead)
def read_item(item_id: int, session: SessionDep) -> ItemRead:
    return ItemRead.of(_get_item(session, item_id), _photos(session, item_id))


@router.put("/items/{item_id}", response_model=ItemRead)
@router.patch("/items/{item_id}", response_model=ItemRead)
def update_item(item_id: int, body: ItemFields, session: SessionDep) -> ItemRead:
    """Modification partielle : seuls les champs envoyés sont changés (null efface un champ)."""
    item = _get_item(session, item_id)
    changes = body.model_dump(exclude_unset=True)
    if changes.get("numista_id", item.numista_id) != item.numista_id and "numista_title" not in changes:
        changes["numista_title"] = None  # le titre de l'ancienne fiche ne correspond plus
    item.sqlmodel_update(changes)
    _fill_numista_url(item)
    item.updated_at = utcnow()
    session.add(item)
    session.commit()
    session.refresh(item)
    return ItemRead.of(item, _photos(session, item_id))


@router.delete("/items/{item_id}", status_code=204)
def delete_item(
    item_id: int, session: SessionDep, state: StateDep
) -> Response:
    item = _get_item(session, item_id)
    session.exec(delete(Photo).where(col(Photo.item_id) == item_id))
    session.delete(item)
    session.commit()
    storage.delete_item_photos(state.data_dir, item_id)
    return Response(status_code=204)


# --- photos ------------------------------------------------------------------------------------


@router.post("/items/{item_id}/photos", response_model=PhotoRead, status_code=201)
def add_photo(
    item_id: int,
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    state: StateDep,
    side: Annotated[PhotoSide, Form()] = PhotoSide.other,
) -> PhotoRead:
    item = _get_item(session, item_id)
    # Route synchrone : lecture, vérification et miniature tournent hors de la boucle asyncio
    path, thumb = storage.store_item_photo(state.data_dir, item_id, side.value, file.file.read())
    photo = Photo(item_id=item_id, side=side, path=path, thumb_path=thumb)
    item.updated_at = utcnow()
    session.add(photo)
    session.add(item)
    session.commit()
    session.refresh(photo)
    return PhotoRead.of(photo)


@router.delete("/items/{item_id}/photos/{photo_id}", status_code=204)
def delete_photo(
    item_id: int, photo_id: int,
    session: SessionDep, state: StateDep,
) -> Response:
    photo = session.get(Photo, photo_id)
    if photo is None or photo.item_id != item_id:
        raise HTTPException(404, "Photo introuvable")
    session.delete(photo)
    session.commit()
    storage.delete_files(state.data_dir, photo.path, photo.thumb_path)
    return Response(status_code=204)


def _photo_file(photo_id: int, session: Session, state: AppState, thumb: bool) -> FileResponse:
    photo = session.get(Photo, photo_id)
    if photo is None:
        raise HTTPException(404, "Photo introuvable")
    try:
        path = storage.resolve(state.data_dir, photo.thumb_path if thumb else photo.path)
    except storage.PhotoError as exc:
        raise HTTPException(404, str(exc)) from exc
    if not path.is_file():
        raise HTTPException(404, "Fichier de la photo absent du disque")
    # Le contenu d'une photo ne change jamais (nouveau nom à chaque ajout) : cache long
    return FileResponse(path, headers={"Cache-Control": "private, max-age=31536000, immutable"})


@router.get("/photos/{photo_id}")
def photo_file(
    photo_id: int, session: SessionDep, state: StateDep
) -> FileResponse:
    return _photo_file(photo_id, session, state, thumb=False)


@router.get("/photos/{photo_id}/thumb")
def photo_thumb(
    photo_id: int, session: SessionDep, state: StateDep
) -> FileResponse:
    return _photo_file(photo_id, session, state, thumb=True)
