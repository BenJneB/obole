"""Export de la collection (CSV, JSON) et sauvegardes complètes (ZIP : base SQLite + photos)."""

import asyncio
import csv
import io
import json
import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
from sqlmodel import Session, col, select
from starlette.background import BackgroundTask

from .. import backup
from ..deps import AppState, SessionDep, StateDep
from ..models import Item
from .items import photos_by_item

router = APIRouter(tags=["export"])

CSV_FIELDS = [name for name in Item.model_fields if name != "ai_raw_json"] + ["photos"]


def _rows(session: Session) -> list[dict]:
    photos = photos_by_item(session)
    rows = []
    for item in session.exec(select(Item).order_by(col(Item.id))):
        row = item.model_dump(mode="json")
        row["ai_extraction"] = json.loads(row.pop("ai_raw_json")) if item.ai_raw_json else None
        row["photos"] = [photo.path for photo in photos.get(item.id, [])]
        rows.append(row)
    return rows


def _filename(ext: str) -> str:
    return f"obole-{datetime.now():%Y%m%d-%H%M%S}.{ext}"


@router.get("/export.csv")
def export_csv(session: SessionDep) -> Response:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=CSV_FIELDS, extrasaction="ignore", lineterminator="\r\n")
    writer.writeheader()
    for row in _rows(session):
        writer.writerow({**row, "photos": " ".join(row["photos"])})
    # BOM UTF-8 : Excel (Windows) affiche alors correctement les accents
    return Response(
        "\ufeff" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{_filename("csv")}"'},
    )


@router.get("/export.json")
def export_json(session: SessionDep) -> Response:
    payload = {"exported_at": datetime.now().isoformat(timespec="seconds"), "items": _rows(session)}
    return Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{_filename("json")}"'},
    )


@router.get("/backup.zip")
def backup_zip(state: StateDep) -> FileResponse:
    """Sauvegarde téléchargée à la demande. Le fichier temporaire est supprimé après l'envoi."""
    tmp_dir = state.data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, zip_name = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
    os.close(fd)
    zip_path = Path(zip_name)
    try:
        backup.write_backup(state.data_dir, zip_path)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=_filename("zip").replace("obole-", "obole-backup-"),
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )


# --- sauvegardes automatiques -----------------------------------------------------------------


class BackupFile(BaseModel):
    name: str
    created_at: datetime
    size: int


class BackupStatus(BaseModel):
    enabled: bool
    interval_hours: float
    keep: int
    directory: str
    last_run_at: datetime | None
    last_status: str | None  # created / unchanged / error
    last_error: str | None
    backups: list[BackupFile]


def backup_status(state: AppState) -> BackupStatus:
    settings = state.settings
    folder = settings.backup_path
    run = backup.last_run(folder)
    at = run.get("at")
    return BackupStatus(
        enabled=settings.backup_interval_hours > 0,
        interval_hours=settings.backup_interval_hours,
        keep=settings.backup_keep,
        directory=str(folder),
        last_run_at=datetime.fromtimestamp(at) if isinstance(at, int | float) else None,
        last_status=run.get("status"),
        last_error=run.get("error"),
        backups=[BackupFile(**vars(b)) for b in backup.list_auto_backups(folder)],
    )


@router.get("/backups", response_model=BackupStatus)
def get_backups(state: StateDep) -> BackupStatus:
    return backup_status(state)


@router.post("/backups", response_model=BackupStatus)
async def backup_now(state: StateDep) -> BackupStatus:
    """Crée tout de suite une sauvegarde dans le dossier des sauvegardes automatiques
    (même si rien n'a changé), avec la même rotation."""
    settings = state.settings
    async with state.backup_lock:
        try:
            await asyncio.to_thread(
                backup.run_auto_backup, state.data_dir, settings.backup_path, state.engine,
                settings.backup_keep, True,
            )
        except (OSError, sqlite3.Error) as exc:
            backup.record_error(settings.backup_path, str(exc))
            raise HTTPException(500, f"Sauvegarde impossible : {exc}") from exc
    return backup_status(state)
