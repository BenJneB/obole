"""Re-classement des candidats Numista par le modèle vision (Phase 4).

Le modèle reçoit deux images :
1. la photo de l'objet (avers et revers côte à côte, recadrés) ;
2. une planche numérotée : une ligne par candidat, miniature Numista de l'avers puis du revers.
Il répond par le numéro de la fiche qui correspond (0 = aucune). Deux images seulement, quel que
soit le nombre de candidats : le contexte reste dans les 4096 tokens d'un GPU de 8 Go.

Biais de position : mesuré sur les photos de test (gemma4:e4b), le modèle désigne la ligne 1 dès
qu'il hésite, avec une « confiance » de 0,95 à 1 même quand il a tort. La question est donc posée
deux fois, candidats dans l'ordre puis dans l'ordre inverse : le choix n'est retenu que si les deux
réponses désignent la même fiche (4/4 justes sur le banc d'essai) ; sinon l'ordre de Numista est
conservé.

Les miniatures viennent du site Numista (pas de l'API : aucun quota consommé) et sont gardées en
cache dans DATA_DIR/cache/thumbs. Seules les adresses *.numista.com sont téléchargées : la liste
des candidats arrive du navigateur, le serveur ne doit pas servir de relais vers n'importe où.
"""

import asyncio
import hashlib
import io
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from . import vision

MAX_CANDIDATES = 5
THUMB_SIDE = 180  # taille des miniatures Numista
LABEL_WIDTH = 90
GAP = 8
MAX_THUMB_BYTES = 2 * 1024 * 1024
ALLOWED_HOST_SUFFIXES = (".numista.com",)

PROMPT = """You are a numismatic expert.
Image 1 is a photo of ONE coin, banknote or token from a collection{sides}.
Image 2 is a sheet of {count} numbered catalogue entries. Each numbered row shows the catalogue \
picture of the obverse (left) and of the reverse (right); a grey square means no picture.
Which numbered entry is the SAME type as the object in image 1? Compare the designs (portrait, \
coat of arms, main motif), the denomination, the inscriptions, the shape and the colour. \
Ignore wear, lighting and photo quality.
- "best": the number of the matching entry, or 0 if none of them matches.
- "confidence": between 0 and 1.
- "reason": one short sentence in French explaining the decisive visual clue.
Answer with the JSON object only."""


def schema(count: int) -> dict:
    return {
        "type": "object",
        "properties": {
            "best": {"type": "integer", "enum": list(range(count + 1))},
            "confidence": {"type": "number"},
            "reason": {"type": "string"},
        },
        "required": ["best", "confidence", "reason"],
    }


@dataclass
class RerankCandidate:
    id: int
    obverse_thumbnail: str | None
    reverse_thumbnail: str | None


@dataclass
class RerankResult:
    order: list[int]  # ids des candidats, le choix du modèle en premier
    # "match" : les deux passes désignent best_id ; "none" : aucune fiche ne correspond selon les
    # deux passes ; "unsure" : réponses différentes, ordre de Numista conservé
    verdict: str
    best_id: int | None
    confidence: float
    reason: str
    model: str
    seconds: float
    missing_thumbnails: int = 0
    answers: list[dict] = field(default_factory=list)  # réponses brutes, une par passe


def allowed_url(url: str | None) -> bool:
    if not url:
        return False
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host.endswith(s) for s in ALLOWED_HOST_SUFFIXES)


async def fetch_thumbnail(
    client: httpx.AsyncClient, url: str | None, cache_dir: Path
) -> Image.Image | None:
    """Miniature Numista (cache disque) ; None si absente, refusée ou illisible."""
    if not allowed_url(url):
        return None
    cache_file = cache_dir / (hashlib.sha256(url.encode()).hexdigest()[:32] + ".img")
    data = cache_file.read_bytes() if cache_file.is_file() else None
    if data is None:
        try:
            response = await client.get(url)
        except httpx.HTTPError:
            return None
        if response.status_code != 200 or len(response.content) > MAX_THUMB_BYTES:
            return None
        data = response.content
    try:
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            image = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError):
        return None
    if not cache_file.is_file():
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_file.write_bytes(data)
    return image


def _font(size: int) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # Pillow sans FreeType : police bitmap, plus petite mais lisible
        return ImageFont.load_default()


def _fit(img: Image.Image | None, side: int) -> Image.Image:
    """Image centrée dans un carré blanc (gris uni si absente)."""
    if img is None:
        return Image.new("RGB", (side, side), (200, 200, 200))
    img = img.copy()
    img.thumbnail((side, side))
    square = Image.new("RGB", (side, side), (255, 255, 255))
    square.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    return square


def candidate_sheet(thumbnails: list[tuple[Image.Image | None, Image.Image | None]]) -> Image.Image:
    """Planche : une ligne par candidat, « n » puis avers et revers."""
    rows = len(thumbnails)
    width = LABEL_WIDTH + 2 * THUMB_SIDE + 2 * GAP
    height = rows * THUMB_SIDE + (rows + 1) * GAP
    sheet = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(sheet)
    font = _font(64)
    for index, (obverse, reverse) in enumerate(thumbnails):
        top = GAP + index * (THUMB_SIDE + GAP)
        if index:
            draw.line([(0, top - GAP // 2), (width, top - GAP // 2)], fill=(160, 160, 160), width=2)
        draw.text((LABEL_WIDTH // 2, top + THUMB_SIDE // 2), str(index + 1), fill=(200, 0, 0),
                  font=font, anchor="mm")
        sheet.paste(_fit(obverse, THUMB_SIDE), (LABEL_WIDTH, top))
        sheet.paste(_fit(reverse, THUMB_SIDE), (LABEL_WIDTH + THUMB_SIDE + GAP, top))
    sheet.thumbnail((vision.MAX_SIDE, vision.MAX_SIDE))
    return sheet


def photo_sheet(paths: list[Path], auto_crop: bool) -> Image.Image:
    """Photos de l'objet côte à côte (avers puis revers), à la même hauteur."""
    images = [vision.load_image(p, vision.MAX_SIDE, auto_crop) for p in paths]
    if len(images) == 1:
        return images[0]
    height = min(img.height for img in images)
    resized = [img.resize((max(1, round(img.width * height / img.height)), height)) for img in images]
    sheet = Image.new("RGB", (sum(img.width for img in resized) + GAP * (len(resized) - 1), height),
                      (255, 255, 255))
    left = 0
    for img in resized:
        sheet.paste(img, (left, 0))
        left += img.width + GAP
    sheet.thumbnail((vision.MAX_SIDE, vision.MAX_SIDE))
    return sheet


async def rerank(
    photos: list[Path],
    candidates: list[RerankCandidate],
    *,
    ollama_url: str,
    model: str,
    num_ctx: int = 4096,
    auto_crop: bool = False,
    cache_dir: Path,
    ollama_transport: httpx.AsyncBaseTransport | None = None,
    web_transport: httpx.AsyncBaseTransport | None = None,  # téléchargement des miniatures (tests)
) -> RerankResult:
    if not 1 <= len(photos) <= 2:
        raise ValueError("1 ou 2 photos attendues (avers, revers)")
    candidates = candidates[:MAX_CANDIDATES]
    if len(candidates) < 2:
        raise ValueError("Au moins 2 candidats à départager")

    async with httpx.AsyncClient(timeout=15, transport=web_transport, follow_redirects=False) as client:
        thumbs = await asyncio.gather(*(
            asyncio.gather(
                fetch_thumbnail(client, c.obverse_thumbnail, cache_dir),
                fetch_thumbnail(client, c.reverse_thumbnail, cache_dir),
            )
            for c in candidates
        ))
    missing = sum(1 for pair in thumbs if pair[0] is None and pair[1] is None)
    if missing == len(candidates):
        raise vision.VisionError("Miniatures Numista indisponibles : re-classement impossible")

    object_image = await asyncio.to_thread(photo_sheet, photos, auto_crop)
    encoded_object = vision.encode_jpeg(object_image)
    sides = " (obverse on the left, reverse on the right)" if len(photos) == 2 else ""
    prompt = PROMPT.format(sides=sides, count=len(candidates))
    ids = [c.id for c in candidates]
    by_id = dict(zip(ids, thumbs, strict=True))

    async def ask(order: list[int]) -> tuple[int | None, dict, vision.VisionResult]:
        """Une passe : candidats dans `order` ; renvoie l'id choisi (None = aucun)."""
        sheet = await asyncio.to_thread(candidate_sheet, [tuple(by_id[i]) for i in order])
        result = await vision.chat_json(
            [encoded_object, vision.encode_jpeg(sheet)], prompt, schema(len(order)),
            ollama_url=ollama_url, model=model, num_ctx=num_ctx, max_output_tokens=256,
            transport=ollama_transport,
        )
        answer = result.extraction
        best = answer.get("best")
        if not isinstance(best, int) or isinstance(best, bool) or not 0 <= best <= len(order):
            raise vision.VisionError(f"Réponse du modèle inattendue : {answer}")
        return (order[best - 1] if best else None), answer, result

    # Séquentiel : Ollama traite de toute façon une requête à la fois sur un seul GPU
    first, answer1, result1 = await ask(ids)
    second, answer2, result2 = await ask(ids[::-1])

    if first == second:
        verdict = "none" if first is None else "match"
        best_id = first
    else:
        verdict, best_id = "unsure", None
    order = ids if best_id is None else [best_id, *(i for i in ids if i != best_id)]
    confidences = [a.get("confidence") for a in (answer1, answer2)]
    confidence = min(float(c) if isinstance(c, int | float) else 0.0 for c in confidences)
    return RerankResult(
        order=order,
        verdict=verdict,
        best_id=best_id,
        confidence=min(1.0, max(0.0, confidence)) if verdict != "unsure" else 0.0,
        reason=str(answer1.get("reason") or "") if verdict != "unsure" else "",
        model=result1.model,
        seconds=result1.seconds + result2.seconds,
        missing_thumbnails=missing,
        answers=[answer1, answer2],
    )
