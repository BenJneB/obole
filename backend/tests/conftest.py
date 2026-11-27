"""Fixtures communes : Ollama et Numista simulés (httpx.MockTransport), données dans un dossier
temporaire. Aucun test n'utilise le réseau, le GPU ni le fichier .env du poste."""

import io
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings
from app.db import init_db, make_engine
from app.factory import create_app
from app.services import vision
from app.services.numista import NumistaClient

OLLAMA_URL = "http://ollama.test:11434"
NUMISTA_URL = "https://numista.test/v3"

EXTRACTION = {
    "object_type": "coin",
    "country": "France",
    "country_confidence": 0.95,
    "currency": "euro",
    "denomination_value": 2,
    "denomination_text": "2 Euro",
    "year": 2012,
    "year_visible": True,
    "inscriptions": ["RF", "2012"],
    "language_of_inscriptions": "fr",
    "description": "Pièce bimétallique, arbre stylisé",
    "is_commemorative_guess": False,
    "search_keywords": ["2 euro", "France", "tree"],
}

ISSUERS = [
    {"code": "france", "name": "France"},
    {"code": "france_section", "name": "France"},
    {"code": "germany", "name": "Germany"},
    {"code": "zone_euro", "name": "Eurozone"},
    {"code": "united-states", "name": "United States"},
]
ISSUERS_FR = [
    {"code": "france", "name": "France"},
    {"code": "germany", "name": "Allemagne"},
    {"code": "zone_euro", "name": "Zone euro"},
    {"code": "united-states", "name": "États-Unis"},
]
OBJECT_TYPES = [
    {"id": 147, "name": "Coins"},
    {"id": 18, "name": "Banknotes"},
    {"id": 3, "name": "Exonumia"},
    {"id": 99, "name": "Circulating coins", "parent": {"id": 147}},
]


def numista_type(type_id: int, title: str = "2 Euro (Arbre)") -> dict:
    return {
        "id": type_id,
        "title": title,
        "object_type": {"id": 147, "name": "Coins"},
        "issuer": {"code": "france", "name": "France"},
        "min_year": 2001,
        "max_year": 2024,
        "obverse_thumbnail": f"https://numista.test/{type_id}-a.jpg",
        "reverse_thumbnail": f"https://numista.test/{type_id}-r.jpg",
    }


class FakeNumista:
    """Numista simulé. `types_handler(params)` décide de la réponse de GET /types."""

    def __init__(self):
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None  # code HTTP à renvoyer pour toute requête
        self.types_handler: Callable[[dict], dict] = lambda params: {
            "count": 2, "types": [numista_type(1001), numista_type(1002, "2 Euro (Marianne)")]
        }

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        assert request.headers["Numista-API-Key"] == "test-key"
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"error_message": "simulé"})
        path = request.url.path.removeprefix("/v3")
        params = dict(request.url.params)
        if path == "/issuers":
            issuers = ISSUERS_FR if params.get("lang") == "fr" else ISSUERS
            return httpx.Response(200, json={"issuers": issuers})
        if path == "/object_types":
            return httpx.Response(200, json={"object_types": OBJECT_TYPES})
        if path == "/types":
            return httpx.Response(200, json=self.types_handler(params))
        if path.startswith("/types/"):
            type_id = int(path.split("/")[2])
            if type_id == 404:
                return httpx.Response(404, json={"error_message": "Type not found"})
            detail = {**numista_type(type_id), "composition": {"text": "Bimétallique"}}
            return httpx.Response(200, json=detail)
        return httpx.Response(404, json={"error_message": "route inconnue"})

    def paths(self) -> list[str]:
        return [r.url.path.removeprefix("/v3") for r in self.requests]

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class FakeOllama:
    def __init__(self):
        self.extraction: dict = dict(EXTRACTION)
        self.capabilities = ["completion", "vision"]
        self.down = False
        self.chat_payloads: list[dict] = []
        self.done_reason = "stop"
        self.raw_content: str | None = None  # contenu brut à renvoyer à la place de l'extraction

    def handler(self, request: httpx.Request) -> httpx.Response:
        if self.down:
            raise httpx.ConnectError("Connection refused", request=request)
        path = request.url.path
        if path == "/api/version":
            return httpx.Response(200, json={"version": "0.99.0"})
        if path == "/api/show":
            return httpx.Response(200, json={"capabilities": self.capabilities})
        if path == "/api/chat":
            payload = json.loads(request.content)
            self.chat_payloads.append(payload)
            content = self.raw_content if self.raw_content is not None else json.dumps(self.extraction)
            return httpx.Response(200, json={
                "message": {"role": "assistant", "content": content},
                "done_reason": self.done_reason,
                "prompt_eval_count": 600,
                "eval_count": 120,
                "load_duration": 0,
            })
        return httpx.Response(404)

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


@pytest.fixture(autouse=True)
def _clear_capabilities_cache():
    vision._capabilities_cache.clear()
    yield
    vision._capabilities_cache.clear()


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    return Settings(
        _env_file=None,
        data_dir=data_dir,
        ollama_url=OLLAMA_URL,
        vision_model="test-vision:1b",
        numista_api_key="test-key",
        numista_base_url=NUMISTA_URL,
        numista_monthly_quota=2000,
    )


@pytest.fixture
def fake_numista() -> FakeNumista:
    return FakeNumista()


@pytest.fixture
def fake_ollama() -> FakeOllama:
    return FakeOllama()


@pytest.fixture
def engine(data_dir: Path):
    engine = make_engine(data_dir)
    init_db(engine, data_dir)
    yield engine
    engine.dispose()


@pytest.fixture
def numista_client(engine, fake_numista: FakeNumista) -> NumistaClient:
    return NumistaClient("test-key", NUMISTA_URL, engine, 2000, transport=fake_numista.transport)


@pytest.fixture
def client(settings: Settings, fake_numista: FakeNumista, fake_ollama: FakeOllama):
    app = create_app(
        settings, numista_transport=fake_numista.transport, ollama_transport=fake_ollama.transport
    )
    with TestClient(app) as test_client:
        yield test_client
    app.state.obole.engine.dispose()  # Windows : libère le fichier SQLite avant la suppression du dossier


def make_image(color=(200, 150, 50), size=(640, 480), fmt="JPEG") -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, fmt)
    return buffer.getvalue()


@pytest.fixture
def jpeg() -> bytes:
    return make_image()
