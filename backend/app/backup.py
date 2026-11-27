"""Sauvegarde complète (ZIP : copie cohérente de la base SQLite + dossier photos).

Utilisée par le téléchargement manuel (/api/backup.zip) et par la sauvegarde automatique
planifiée, qui tourne dans le serveur lui-même (aucune tâche Windows/cron à installer) :
- toutes les BACKUP_INTERVAL_HOURS heures, si la collection a changé depuis la dernière ;
- dans BACKUP_DIR (défaut DATA_DIR/backups), en gardant les BACKUP_KEEP plus récentes.
"""

import asyncio
import json
import logging
import os
import sqlite3
import tempfile
import time
import zipfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, text

from .db import DB_FILENAME

log = logging.getLogger(__name__)

AUTO_PREFIX = "obole-auto-"
STATE_FILE = "derniere-sauvegarde.json"
CHECK_EVERY_SECONDS = 15 * 60  # fréquence de vérification de l'échéance


def write_backup(data_dir: Path, target: Path) -> None:
    """Écrit la sauvegarde dans `target` (créé via un fichier temporaire : jamais de ZIP à moitié écrit)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(suffix=".part", dir=target.parent)
    os.close(fd)  # Windows : un fichier ouvert ne peut pas être rouvert par zipfile
    tmp_zip = Path(tmp_name)
    db_copy = tmp_zip.with_suffix(".sqlite3")
    try:
        # API de sauvegarde SQLite : copie cohérente même pendant une écriture
        source = sqlite3.connect(data_dir / DB_FILENAME)
        copy = sqlite3.connect(db_copy)
        try:
            source.backup(copy)
        finally:
            copy.close()
            source.close()
        with zipfile.ZipFile(tmp_zip, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_copy, DB_FILENAME)
            photos_dir = data_dir / "photos"
            if photos_dir.is_dir():
                for file in sorted(photos_dir.rglob("*")):
                    if file.is_file():
                        # Les JPEG sont déjà compressés : les stocker tels quels va plus vite
                        archive.write(file, file.relative_to(data_dir).as_posix(), zipfile.ZIP_STORED)
        os.replace(tmp_zip, target)
    except BaseException:
        tmp_zip.unlink(missing_ok=True)
        raise
    finally:
        db_copy.unlink(missing_ok=True)


def auto_backup_name() -> str:
    return f"{AUTO_PREFIX}{datetime.now():%Y%m%d-%H%M%S}.zip"


def fingerprint(engine: Engine) -> str:
    """Résumé de l'état de la collection : change dès qu'un objet ou une photo est ajouté,
    modifié ou supprimé (les ids ne sont jamais réutilisés, voir models.py)."""
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT (SELECT count(*) FROM items), (SELECT max(updated_at) FROM items),"
            " (SELECT count(*) FROM photos), (SELECT max(id) FROM photos)"
        )).one()
    return json.dumps(list(row), default=str)


@dataclass
class BackupInfo:
    name: str
    created_at: datetime
    size: int


def list_auto_backups(backup_dir: Path) -> list[BackupInfo]:
    """Sauvegardes automatiques présentes, de la plus récente à la plus ancienne."""
    if not backup_dir.is_dir():
        return []
    found = []
    for file in backup_dir.glob(f"{AUTO_PREFIX}*.zip"):
        stat = file.stat()
        found.append(BackupInfo(file.name, datetime.fromtimestamp(stat.st_mtime), stat.st_size))
    # le nom contient la date à la seconde : ordre fiable même si les mtimes sont identiques
    return sorted(found, key=lambda b: b.name, reverse=True)


def _read_state(backup_dir: Path) -> dict:
    try:
        return json.loads((backup_dir / STATE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _write_state(backup_dir: Path, state: dict) -> None:
    (backup_dir / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


def last_run(backup_dir: Path) -> dict:
    """Dernière vérification : {"at": horodatage, "status": "created"|"unchanged"|"error", ...}."""
    return _read_state(backup_dir).get("last_run", {})


def run_auto_backup(data_dir: Path, backup_dir: Path, engine: Engine, keep: int, force: bool = False) -> str:
    """Crée une sauvegarde automatique si la collection a changé (ou si `force`), puis supprime les
    plus anciennes au-delà de `keep`. Renvoie "created" ou "unchanged". Fonction bloquante."""
    backup_dir.mkdir(parents=True, exist_ok=True)
    state = _read_state(backup_dir)
    current = fingerprint(engine)
    existing = list_auto_backups(backup_dir)
    if not force and existing and state.get("fingerprint") == current:
        status = "unchanged"
    else:
        name = auto_backup_name()
        write_backup(data_dir, backup_dir / name)
        state["fingerprint"] = current
        status = "created"
        log.info("Sauvegarde automatique créée : %s", backup_dir / name)
        for old in list_auto_backups(backup_dir)[max(keep, 1):]:
            (backup_dir / old.name).unlink(missing_ok=True)
            log.info("Ancienne sauvegarde supprimée : %s", old.name)
    state["last_run"] = {"at": time.time(), "status": status}
    _write_state(backup_dir, state)
    return status


def record_error(backup_dir: Path, message: str) -> None:
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        state = _read_state(backup_dir)
        state["last_run"] = {"at": time.time(), "status": "error", "error": message}
        _write_state(backup_dir, state)
    except OSError:
        pass


def is_due(backup_dir: Path, interval_hours: float) -> bool:
    run = last_run(backup_dir)
    at = run.get("at")
    if not isinstance(at, int | float) or run.get("status") == "error":
        return True  # jamais faite, ou échec : on réessaie au prochain passage
    return time.time() - at >= interval_hours * 3600


async def scheduler(data_dir: Path, backup_dir: Path, engine: Engine, interval_hours: float, keep: int,
                    lock: asyncio.Lock) -> None:
    """Boucle de fond (lancée au démarrage du serveur) : vérifie régulièrement si une sauvegarde est
    due. Le premier passage a lieu au démarrage : un PC éteint plusieurs jours rattrape aussitôt."""
    while True:
        if is_due(backup_dir, interval_hours):
            try:
                async with lock:
                    await asyncio.to_thread(run_auto_backup, data_dir, backup_dir, engine, keep)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # ne jamais arrêter la boucle (disque plein, dossier absent...)
                log.exception("Échec de la sauvegarde automatique")
                record_error(backup_dir, str(exc) or type(exc).__name__)
        await asyncio.sleep(CHECK_EVERY_SECONDS)
