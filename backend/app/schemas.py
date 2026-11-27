"""Schémas d'entrée/sortie de l'API (distincts des tables)."""

import json
from datetime import datetime

from pydantic import BaseModel
from sqlmodel import SQLModel

from .models import Item, ItemFields, Photo, PhotoSide


class PhotoRead(BaseModel):
    id: int
    side: PhotoSide
    url: str
    thumb_url: str

    @classmethod
    def of(cls, photo: Photo) -> "PhotoRead":
        return cls(
            id=photo.id,
            side=photo.side,
            url=f"/api/photos/{photo.id}",
            thumb_url=f"/api/photos/{photo.id}/thumb",
        )


class ItemCreate(ItemFields):
    # Photos d'une identification (/api/identify) à rattacher à l'objet
    upload_id: str | None = None
    # Extraction brute renvoyée par /api/identify, conservée telle quelle
    ai_extraction: dict | None = None


class ItemRead(ItemFields):
    id: int
    created_at: datetime
    updated_at: datetime
    ai_extraction: dict | None = None
    photos: list[PhotoRead] = []

    @classmethod
    def of(cls, item: Item, photos: list[Photo]) -> "ItemRead":
        return cls(
            **item.model_dump(exclude={"ai_raw_json"}),
            ai_extraction=json.loads(item.ai_raw_json) if item.ai_raw_json else None,
            photos=[PhotoRead.of(p) for p in photos],
        )


class ItemList(BaseModel):
    total: int
    items: list[ItemRead]


class CandidateRead(BaseModel):
    id: int
    title: str
    issuer: str | None
    years: str
    url: str
    obverse_thumbnail: str | None
    reverse_thumbnail: str | None


class MatchRead(BaseModel):
    issuer_code: str | None
    issuer_name: str | None
    issuer_score: float
    queries: list[dict]
    candidates: list[CandidateRead]


class QuotaRead(BaseModel):
    used: int
    limit: int
    remaining: int


class IdentifyResponse(BaseModel):
    upload_id: str
    sides: list[PhotoSide]
    extraction: dict | None = None
    model: str | None = None
    seconds: float | None = None
    match: MatchRead | None = None
    # Messages d'erreur à afficher ; l'identification peut être partielle (repli : saisie manuelle)
    errors: list[str] = []
    quota: QuotaRead


class Stats(SQLModel):
    items: int
    total_quantity: int
    countries: int
    by_type: dict[str, int]
    by_country: dict[str, int]  # pays -> nombre d'objets, du plus fréquent au moins fréquent
    # année -> nombre d'objets (le frontend regroupe par décennie ou par siècle) ; sans année à part
    by_year: dict[int, int]
    without_year: int
    by_condition: dict[str, int]  # état -> nombre d'objets ("" = non précisé)
    by_tag: dict[str, int]  # tag -> nombre d'objets, du plus fréquent au moins fréquent
    identified: int  # objets rattachés à une fiche Numista
    estimated_value_total: float  # somme de valeur estimée × quantité
    acquired_price_total: float  # somme des prix payés (prix saisi = prix du lot)
