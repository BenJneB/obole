"""Tables SQLite (SQLModel).

Toute modification de schéma doit être accompagnée d'une migration dans db.py.
"""

from datetime import UTC, date, datetime
from enum import StrEnum

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


class ObjectType(StrEnum):
    coin = "coin"
    banknote = "banknote"
    token = "token"
    unknown = "unknown"


class PhotoSide(StrEnum):
    obverse = "obverse"
    reverse = "reverse"
    other = "other"


class IdentificationSource(StrEnum):
    ai_numista = "ai_numista"  # proposé par l'IA, confirmé par moi
    numista = "numista"  # choisi par recherche manuelle dans Numista
    manual = "manual"  # saisie libre


# --- Collection ----------------------------------------------------------------------


class ItemFields(SQLModel):
    """Champs modifiables d'un objet (partagés entre la table et les schémas d'API)."""

    object_type: ObjectType = ObjectType.coin
    country: str | None = None
    issuer_code: str | None = None  # code émetteur Numista (ex. "france", "ancienne_urss")
    denomination: str | None = None
    currency: str | None = None
    year: int | None = None
    mint_mark: str | None = None

    numista_id: int | None = Field(default=None, index=True)
    numista_title: str | None = None
    numista_url: str | None = None

    condition: str | None = None  # UNC, SUP, TTB, TB, B...
    quantity: int = Field(default=1, ge=0)
    acquired_date: date | None = None
    acquired_price: float | None = None
    estimated_value: float | None = None  # valeur estimée par exemplaire
    storage_location: str | None = None
    notes: str | None = None
    tags: str | None = None  # séparés par des virgules

    identification_source: IdentificationSource = IdentificationSource.manual


class Item(ItemFields, table=True):
    __tablename__ = "items"
    # AUTOINCREMENT : un id supprimé n'est jamais réattribué (URL de photos mises en cache, liens)
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    ai_raw_json: str | None = None  # extraction brute du modèle, pour pouvoir y revenir
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)


class Photo(SQLModel, table=True):
    __tablename__ = "photos"
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    item_id: int = Field(foreign_key="items.id", index=True, ondelete="CASCADE")
    side: PhotoSide = PhotoSide.other
    # Chemins relatifs à DATA_DIR, toujours avec "/" (portables Ubuntu <-> Windows)
    path: str
    thumb_path: str
    created_at: datetime = Field(default_factory=utcnow)


# --- Numista -----------------------------------------------------------------------------


class NumistaCache(SQLModel, table=True):
    __tablename__ = "numista_cache"

    key: str = Field(primary_key=True)  # chemin + paramètres triés
    response_json: str
    fetched_at: datetime = Field(default_factory=utcnow)


class ApiUsage(SQLModel, table=True):
    __tablename__ = "api_usage"

    month: str = Field(primary_key=True)  # "2026-11"
    numista_requests: int = 0
