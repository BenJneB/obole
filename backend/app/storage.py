"""Stockage des photos sur disque.

- Envois en attente (photos d'une identification pas encore enregistrée) :
  DATA_DIR/uploads/<upload_id>/<side>.<ext>, supprimés après UPLOAD_TTL.
- Photos de la collection : DATA_DIR/photos/<item_id>/<n>_<side>.<ext> + miniature <n>_<side>_thumb.jpg.

L'original est gardé tel quel (aucune perte de qualité) ; seule la miniature est recalculée.
Les chemins enregistrés en base sont relatifs à DATA_DIR et utilisent "/" (portables).
"""

import io
import re
import shutil
import time
import uuid
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
THUMB_SIDE = 400
UPLOAD_TTL_SECONDS = 24 * 3600
FORMAT_EXTENSIONS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}
UPLOAD_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Pillow refuse par défaut les images géantes (bombe de décompression) : limite explicite
Image.MAX_IMAGE_PIXELS = 80_000_000


class PhotoError(ValueError):
    pass


def check_image(data: bytes) -> str:
    """Vérifie que les octets sont une image JPEG/PNG/WebP ; renvoie l'extension."""
    if not data:
        raise PhotoError("Fichier vide")
    if len(data) > MAX_UPLOAD_BYTES:
        raise PhotoError(f"Photo trop lourde (> {MAX_UPLOAD_BYTES // 1024 // 1024} Mo)")
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
            fmt = img.format
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as exc:
        raise PhotoError("Fichier illisible : ce n'est pas une photo JPEG, PNG ou WebP") from exc
    if fmt not in FORMAT_EXTENSIONS:
        raise PhotoError(f"Format {fmt} non pris en charge (JPEG, PNG ou WebP)")
    return FORMAT_EXTENSIONS[fmt]


def make_thumbnail(source: Path, target: Path) -> None:
    with Image.open(source) as img:
        img.thumbnail((THUMB_SIDE, THUMB_SIDE))  # avant exif_transpose : décodage réduit du JPEG
        img = ImageOps.exif_transpose(img)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.save(target, "JPEG", quality=85)


def rel(data_dir: Path, path: Path) -> str:
    return path.relative_to(data_dir).as_posix()


def resolve(data_dir: Path, relative: str) -> Path:
    """Chemin absolu d'un fichier enregistré en base ; refuse toute sortie de DATA_DIR."""
    path = (data_dir / Path(*relative.split("/"))).resolve()
    if not path.is_relative_to(data_dir.resolve()):
        raise PhotoError("Chemin de photo invalide")
    return path


# --- envois en attente ---------------------------------------------------------------------


def uploads_dir(data_dir: Path) -> Path:
    return data_dir / "uploads"


def save_upload(data_dir: Path, photos: dict[str, bytes]) -> tuple[str, dict[str, Path]]:
    """Enregistre les photos d'une identification ({side: octets}). Renvoie (upload_id, {side: chemin})."""
    checked = {side: (data, check_image(data)) for side, data in photos.items()}
    upload_id = uuid.uuid4().hex
    folder = uploads_dir(data_dir) / upload_id
    folder.mkdir(parents=True)
    paths = {}
    for side, (data, ext) in checked.items():
        path = folder / f"{side}.{ext}"
        path.write_bytes(data)
        paths[side] = path
    return upload_id, paths


def upload_files(data_dir: Path, upload_id: str) -> dict[str, Path]:
    """Photos d'un envoi en attente ({side: chemin}) ; erreur si l'envoi n'existe plus."""
    if not UPLOAD_ID_RE.match(upload_id):
        raise PhotoError("Identifiant d'envoi invalide")
    folder = uploads_dir(data_dir) / upload_id
    if not folder.is_dir():
        raise PhotoError("Photos introuvables (envoi expiré ?) : reprendre les photos")
    return {p.stem: p for p in sorted(folder.iterdir()) if p.is_file()}


def discard_upload(data_dir: Path, upload_id: str) -> None:
    if UPLOAD_ID_RE.match(upload_id):
        shutil.rmtree(uploads_dir(data_dir) / upload_id, ignore_errors=True)


def purge_old_uploads(data_dir: Path, max_age: float = UPLOAD_TTL_SECONDS) -> int:
    folder = uploads_dir(data_dir)
    if not folder.is_dir():
        return 0
    limit = time.time() - max_age
    removed = 0
    for sub in folder.iterdir():
        if sub.is_dir() and sub.stat().st_mtime < limit:
            shutil.rmtree(sub, ignore_errors=True)
            removed += 1
    return removed


# --- photos de la collection -------------------------------------------------------------


def item_dir(data_dir: Path, item_id: int) -> Path:
    return data_dir / "photos" / str(item_id)


def store_item_photo(data_dir: Path, item_id: int, side: str, source: Path | bytes) -> tuple[str, str]:
    """Copie une photo dans le dossier de l'objet et crée sa miniature. Renvoie (path, thumb_path)."""
    data = source.read_bytes() if isinstance(source, Path) else source
    ext = check_image(data)
    folder = item_dir(data_dir, item_id)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid.uuid4().hex[:8]}_{side}"
    path = folder / f"{stem}.{ext}"
    thumb = folder / f"{stem}_thumb.jpg"
    path.write_bytes(data)
    try:
        make_thumbnail(path, thumb)
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return rel(data_dir, path), rel(data_dir, thumb)


def delete_item_photos(data_dir: Path, item_id: int) -> None:
    shutil.rmtree(item_dir(data_dir, item_id), ignore_errors=True)


def delete_files(data_dir: Path, *relatives: str) -> None:
    for relative in relatives:
        try:
            resolve(data_dir, relative).unlink(missing_ok=True)
        except PhotoError:
            pass
