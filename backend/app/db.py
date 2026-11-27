"""Connexion SQLite et migrations simples.

La version du schéma est stockée dans `PRAGMA user_version`. Chaque migration est une fonction
appliquée une seule fois, dans l'ordre. Pour faire évoluer le schéma : modifier models.py, puis
AJOUTER une fonction à la fin de MIGRATIONS (ne jamais modifier une migration déjà livrée).
Exemple : `conn.exec_driver_sql("ALTER TABLE items ADD COLUMN weight REAL")`.
"""

import json
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Connection, Engine, event
from sqlmodel import SQLModel, create_engine

from . import models  # noqa: F401  (enregistre les tables dans SQLModel.metadata)

log = logging.getLogger(__name__)

DB_FILENAME = "obole.sqlite3"


def make_engine(data_dir: Path) -> Engine:
    data_dir.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{(data_dir / DB_FILENAME).as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA journal_mode = WAL")  # lectures pendant une écriture
        cursor.close()
        # lower() de SQLite ne connaît que l'ASCII (« É » reste « É ») : remplacé par celui de Python,
        # pour que toutes les comparaisons sans casse (filtres, ilike, stats) gèrent les accents
        dbapi_conn.create_function(
            "lower", 1, lambda v: v.lower() if isinstance(v, str) else v, deterministic=True
        )

    return engine


# --- migrations ----------------------------------------------------------------------------


def _m1_initial_schema(conn: Connection, data_dir: Path) -> None:
    SQLModel.metadata.create_all(conn)


def _m2_import_poc_cache(conn: Connection, data_dir: Path) -> None:
    """Reprend le cache fichier et le compteur de quota du POC (Phase 0) : évite de
    re-consommer le quota Numista pour des réponses déjà obtenues."""
    cache_dir = data_dir / "cache"
    imported = 0
    for file in sorted((cache_dir / "numista").glob("*.json")):
        try:
            cached = json.loads(file.read_text(encoding="utf-8"))
            key = cache_key(cached["path"], cached["params"])
            fetched = datetime.fromtimestamp(cached["fetched_at"], UTC).replace(tzinfo=None)
            conn.exec_driver_sql(
                "INSERT OR IGNORE INTO numista_cache (key, response_json, fetched_at) VALUES (?, ?, ?)",
                (key, json.dumps(cached["response"], ensure_ascii=False), fetched.isoformat(" ")),
            )
            imported += 1
        except (OSError, ValueError, KeyError) as exc:
            log.warning("Cache POC ignoré (%s) : %s", file.name, exc)
    usage_file = cache_dir / "numista_usage.json"
    if usage_file.is_file():
        try:
            for month, count in json.loads(usage_file.read_text(encoding="utf-8")).items():
                conn.exec_driver_sql(
                    "INSERT INTO api_usage (month, numista_requests) VALUES (?, ?) "
                    "ON CONFLICT(month) DO UPDATE "
                    "SET numista_requests = max(numista_requests, excluded.numista_requests)",
                    (month, int(count)),
                )
        except (OSError, ValueError) as exc:
            log.warning("Compteur de quota du POC ignoré : %s", exc)
    if imported:
        log.info("%d réponse(s) Numista reprise(s) du cache du POC", imported)


def _m3_normalize_tags(conn: Connection, data_dir: Path) -> None:
    """Phase 3 : tags au format normalisé (voir models.normalize_tags), requis par le filtre par tag."""
    rows = conn.exec_driver_sql("SELECT id, tags FROM items WHERE tags IS NOT NULL").all()
    for item_id, tags in rows:
        normalized = models.normalize_tags(tags)
        if normalized != tags:
            conn.exec_driver_sql("UPDATE items SET tags = ? WHERE id = ?", (normalized, item_id))


MIGRATIONS: list[Callable[[Connection, Path], None]] = [
    _m1_initial_schema,
    _m2_import_poc_cache,
    _m3_normalize_tags,
]


def init_db(engine: Engine, data_dir: Path) -> int:
    """Applique les migrations manquantes. Renvoie la version du schéma."""
    with engine.begin() as conn:
        version = conn.exec_driver_sql("PRAGMA user_version").scalar_one()
        for number, migration in enumerate(MIGRATIONS, start=1):
            if number <= version:
                continue
            log.info("Migration %d : %s", number, migration.__name__)
            migration(conn, data_dir)
            # PRAGMA n'accepte pas de paramètre lié ; number est un entier contrôlé
            conn.exec_driver_sql(f"PRAGMA user_version = {number}")
        return len(MIGRATIONS)


def cache_key(path: str, params: dict) -> str:
    """Clé de cache Numista : chemin + paramètres non nuls triés."""
    clean = {k: v for k, v in params.items() if v is not None}
    return path + "?" + json.dumps(clean, sort_keys=True, ensure_ascii=False)
