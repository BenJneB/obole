"""Export de la collection (CSV, JSON) et sauvegarde complète (ZIP : base SQLite + photos)."""

import csv
import io
import json
import os
import sqlite3
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import FileResponse, Response
from sqlmodel import Session, col, select
from starlette.background import BackgroundTask

from ..db import DB_FILENAME
from ..deps import SessionDep, StateDep
from ..models import Item, Photo

router = APIRouter(tags=["export"])

CSV_FIELDS = [name for name in Item.model_fields if name != "ai_raw_json"] + ["photos"]


def _rows(session: Session) -> list[dict]:
    photos: dict[int, list[str]] = {}
    for photo in session.exec(select(Photo).order_by(col(Photo.id))):
        photos.setdefault(photo.item_id, []).append(photo.path)
    rows = []
    for item in session.exec(select(Item).order_by(col(Item.id))):
        row = item.model_dump(mode="json")
        row["ai_extraction"] = json.loads(row.pop("ai_raw_json")) if item.ai_raw_json else None
        row["photos"] = photos.get(item.id, [])
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
    """Copie cohérente de la base (API de sauvegarde SQLite, sûre même pendant une écriture)
    + tout le dossier photos. Le fichier temporaire est supprimé après l'envoi."""
    data_dir = state.data_dir
    tmp_dir = data_dir / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, zip_name = tempfile.mkstemp(suffix=".zip", dir=tmp_dir)
    os.close(fd)  # Windows : un fichier ouvert ne peut pas être rouvert par zipfile
    zip_path = Path(zip_name)
    db_copy = zip_path.with_suffix(".sqlite3")
    try:
        source = sqlite3.connect(data_dir / DB_FILENAME)
        target = sqlite3.connect(db_copy)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(db_copy, DB_FILENAME)
            photos_dir = data_dir / "photos"
            if photos_dir.is_dir():
                for file in sorted(photos_dir.rglob("*")):
                    if file.is_file():
                        # Les JPEG sont déjà compressés : les stocker tels quels va plus vite
                        archive.write(file, file.relative_to(data_dir).as_posix(), zipfile.ZIP_STORED)
    except Exception:
        zip_path.unlink(missing_ok=True)
        raise
    finally:
        db_copy.unlink(missing_ok=True)
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=_filename("zip").replace("obole-", "obole-backup-"),
        background=BackgroundTask(zip_path.unlink, missing_ok=True),
    )
