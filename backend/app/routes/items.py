"""Collection : création, liste filtrée, lecture, modification, suppression, photos."""

import json
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from sqlalchemy import delete, func, or_
from sqlmodel import Session, col, select

from .. import storage
from ..deps import AppState, SessionDep, StateDep
from ..models import Item, ItemFields, Photo, PhotoSide, utcnow
from ..schemas import ItemCreate, ItemList, ItemRead, PhotoRead
from ..services.numista import type_url

router = APIRouter(tags=["collection"])

SORTABLE = {"created_at", "updated_at", "year", "country", "denomination", "estimated_value", "quantity"}
SEARCHED = ("country", "denomination", "currency", "numista_title", "notes", "tags", "storage_location")


def _photos(session: Session, item_id: int) -> list[Photo]:
    return list(session.exec(select(Photo).where(Photo.item_id == item_id).order_by(col(Photo.id))))


def _get_item(session: Session, item_id: int) -> Item:
    item = session.get(Item, item_id)
    if item is None:
        raise HTTPException(404, f"Objet {item_id} introuvable")
    return item


def _fill_numista_url(item: Item) -> None:
    if item.numista_id and not item.numista_url:
        item.numista_url = type_url(item.numista_id)


@router.post("/items", response_model=ItemRead, status_code=201)
def create_item(
    body: ItemCreate, session: SessionDep, state: StateDep
) -> ItemRead:
    uploads = {}
    if body.upload_id:
        try:
            uploads = storage.upload_files(state.data_dir, body.upload_id)
        except storage.PhotoError as exc:
            raise HTTPException(400, str(exc)) from exc

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
    sort: str = Query("-created_at", description="champ de tri, préfixe « - » pour décroissant"),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
) -> ItemList:
    conditions = []
    if q:
        pattern = f"%{q.strip()}%"
        conditions.append(or_(*(col(getattr(Item, name)).ilike(pattern) for name in SEARCHED)))
    if country:
        conditions.append(func.lower(Item.country) == country.strip().lower())
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
    photos: dict[int, list[Photo]] = {item.id: [] for item in items}
    if items:
        for photo in session.exec(
            select(Photo).where(col(Photo.item_id).in_(photos)).order_by(col(Photo.id))
        ):
            photos[photo.item_id].append(photo)
    return ItemList(total=total, items=[ItemRead.of(item, photos[item.id]) for item in items])


@router.get("/items/{item_id}", response_model=ItemRead)
def read_item(item_id: int, session: SessionDep) -> ItemRead:
    return ItemRead.of(_get_item(session, item_id), _photos(session, item_id))


@router.put("/items/{item_id}", response_model=ItemRead)
@router.patch("/items/{item_id}", response_model=ItemRead)
def update_item(item_id: int, body: ItemFields, session: SessionDep) -> ItemRead:
    """Modification partielle : seuls les champs envoyés sont changés (null efface un champ)."""
    item = _get_item(session, item_id)
    changes = body.model_dump(exclude_unset=True)
    if "numista_id" in changes and "numista_url" not in changes:
        item.numista_url = None  # recalculé ci-dessous pour le nouveau N#
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
async def add_photo(
    item_id: int,
    file: Annotated[UploadFile, File()],
    session: SessionDep,
    state: StateDep,
    side: Annotated[PhotoSide, Form()] = PhotoSide.other,
) -> PhotoRead:
    item = _get_item(session, item_id)
    try:
        path, thumb = storage.store_item_photo(state.data_dir, item_id, side.value, await file.read())
    except storage.PhotoError as exc:
        raise HTTPException(400, str(exc)) from exc
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
