"""Client du modèle vision local (Ollama) : préparation des images, prompt et sortie structurée."""

import base64
import io
import json
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from PIL import Image, ImageOps

MAX_SIDE = 768  # côté long envoyé au modèle (px) : ~900 tokens par image, tient en VRAM avec num_ctx 4096

# Schéma JSON imposé à la réponse du modèle (paramètre "format" d'Ollama).
EXTRACTION_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "object_type": {"type": "string", "enum": ["coin", "banknote", "token", "unknown"]},
        "country": {"type": ["string", "null"]},
        "country_confidence": {"type": "number"},
        "currency": {"type": ["string", "null"]},
        "denomination_value": {"type": ["number", "null"]},
        "denomination_text": {"type": ["string", "null"]},
        "year": {"type": ["integer", "null"]},
        "year_visible": {"type": "boolean"},
        "inscriptions": {"type": "array", "items": {"type": "string"}},
        "language_of_inscriptions": {"type": ["string", "null"]},
        "description": {"type": "string"},
        "is_commemorative_guess": {"type": "boolean"},
        "search_keywords": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "object_type", "country", "country_confidence", "currency", "denomination_value",
        "denomination_text", "year", "year_visible", "inscriptions", "language_of_inscriptions",
        "description", "is_commemorative_guess", "search_keywords",
    ],
}

# Prompt en anglais : les modèles open source suivent mieux les consignes en anglais.
PROMPT = """You are a numismatic expert. The image(s) show ONE coin, banknote or token.
{sides}
Extract only what is actually visible. Rules:
- Never invent anything. If a piece of information is not readable, use null.
- "inscriptions": transcribe every readable inscription exactly as written (original script, \
original spelling, keep accents and non-Latin characters).
- "country": the issuing country or state in English, using its historical name if it no longer \
exists (e.g. "Soviet Union", "Roman Empire"). Deduce it from the inscriptions, language and symbols.
- "country_confidence": between 0 and 1.
- "currency": currency name in English, singular (e.g. "euro", "franc", "yen", "kopeck", "dollar", \
"rupee", "denarius").
- "denomination_text": face value in English as a catalogue would write it (e.g. "2 Euro", \
"20 Kopecks", "100 Yen", "1 Dollar", "500 Rupees").
- "year": the Gregorian year of issue if a date is visible (convert other calendars, e.g. \
Japanese era Showa 42 = 1967), otherwise null. "year_visible": true only if a date is printed.
- "search_keywords": 2 to 5 short English keywords useful to search a coin catalogue \
(denomination, country, ruler or main design).
- "description": one short sentence describing the design, in French.
Answer with the JSON object only."""

SIDES_TWO = "The first image is the obverse, the second image is the reverse of the same object."
SIDES_ONE = "Only one image is provided (it may show one side, or both sides next to each other)."


def prepare_image(path: Path) -> str:
    """Redresse (EXIF), convertit en RGB, réduit à MAX_SIDE px et renvoie le JPEG en base64."""
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            background = Image.new("RGB", img.size, (255, 255, 255))
            background.paste(img, mask=img.getchannel("A"))
            img = background
        img = img.convert("RGB")
        img.thumbnail((MAX_SIDE, MAX_SIDE))
        buffer = io.BytesIO()
        img.save(buffer, "JPEG", quality=90)
    return base64.b64encode(buffer.getvalue()).decode("ascii")


@dataclass
class VisionResult:
    extraction: dict
    model: str
    seconds: float  # temps total vu par le client
    prompt_tokens: int | None = None  # tokens d'entrée (images comprises) : à comparer à num_ctx
    output_tokens: int | None = None
    load_seconds: float | None = None  # temps de chargement du modèle (0 s'il était déjà en mémoire)
    raw: dict = field(default_factory=dict)


class VisionError(RuntimeError):
    pass


_capabilities_cache: dict[tuple[str, str], list[str]] = {}


async def model_capabilities(ollama_url: str, model: str) -> list[str]:
    """Capacités annoncées par Ollama ("vision", "thinking"...), mémorisées par modèle."""
    key = (ollama_url, model)
    if key not in _capabilities_cache:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                response = await client.post(f"{ollama_url.rstrip('/')}/api/show", json={"model": model})
        except httpx.HTTPError as exc:
            raise VisionError(f"Ollama injoignable sur {ollama_url} : {exc}") from exc
        if response.status_code != 200:
            raise VisionError(
                f"Modèle {model} absent d'Ollama ({response.status_code}) : ollama pull {model}"
            )
        _capabilities_cache[key] = response.json().get("capabilities") or []
    return _capabilities_cache[key]


async def identify(
    images: list[Path],
    *,
    ollama_url: str,
    model: str,
    num_ctx: int = 4096,
    timeout: float = 300.0,
) -> VisionResult:
    """Envoie 1 ou 2 photos (avers, revers) au modèle et renvoie l'extraction structurée."""
    if not 1 <= len(images) <= 2:
        raise ValueError("1 ou 2 images attendues (avers, revers)")
    capabilities = await model_capabilities(ollama_url, model)
    if "vision" not in capabilities:
        raise VisionError(f"Le modèle {model} ne lit pas les images (capacités : {capabilities})")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": PROMPT.format(sides=SIDES_TWO if len(images) == 2 else SIDES_ONE),
                "images": [prepare_image(p) for p in images],
            }
        ],
        "format": EXTRACTION_SCHEMA,
        "stream": False,
        "keep_alive": "30m",
        "options": {"temperature": 0, "num_ctx": num_ctx},
    }
    if "thinking" in capabilities:
        # Phase de "réflexion" désactivée : bien plus lente, et inutile pour une extraction de texte
        payload["think"] = False
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(f"{ollama_url.rstrip('/')}/api/chat", json=payload)
    except httpx.HTTPError as exc:
        raise VisionError(f"Ollama injoignable sur {ollama_url} : {exc}") from exc
    elapsed = time.perf_counter() - start
    if response.status_code != 200:
        raise VisionError(f"Ollama a répondu {response.status_code} : {response.text[:300]}")
    data = response.json()
    content = data.get("message", {}).get("content", "")
    try:
        extraction = json.loads(content)
    except json.JSONDecodeError as exc:
        raise VisionError(f"Réponse du modèle non JSON : {content[:300]}") from exc
    return VisionResult(
        extraction=extraction,
        model=model,
        seconds=elapsed,
        prompt_tokens=data.get("prompt_eval_count"),
        output_tokens=data.get("eval_count"),
        load_seconds=(data.get("load_duration") or 0) / 1e9,
        raw={k: v for k, v in data.items() if k != "message"},
    )
