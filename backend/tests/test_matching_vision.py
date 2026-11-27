"""Stratégie de recherche Numista et client Ollama (simulé)."""

import asyncio

import pytest

from app.services import matching, vision

from .conftest import EXTRACTION, ISSUERS, ISSUERS_FR, OLLAMA_URL

# --- correspondance pays -> émetteur ----------------------------------------------------------


@pytest.fixture
def index() -> matching.IssuerIndex:
    return matching.IssuerIndex(ISSUERS, ISSUERS_FR)


@pytest.mark.parametrize(
    ("country", "code"),
    [
        ("France", "france"),  # pays préféré au regroupement "france_section"
        ("Allemagne", "germany"),  # nom français
        ("USA", "united-states"),  # alias
        ("European Union", "zone_euro"),
        ("Germny", "germany"),  # faute de frappe : correspondance floue
        ("Atlantis", None),
        (None, None),
    ],
)
def test_issuer_match(index, country, code):
    assert index.match(country)[0] == code


# --- requêtes ----------------------------------------------------------------------------------


def test_queries_precise_to_broad():
    queries = matching.build_queries(EXTRACTION, "france", 147)
    assert queries[0] == {"issuer": "france", "object_type": 147, "q": "2 Euro", "date": "2012"}
    assert queries[1] == {"issuer": "france", "object_type": 147, "q": "2 Euro"}
    assert len(queries) == matching.MAX_SEARCHES


def test_year_not_visible_is_not_used():
    queries = matching.build_queries({**EXTRACTION, "year_visible": False}, "france", 147)
    assert all("date" not in q or q["date"] is None for q in queries)


def test_commemorative_searches_subject_first():
    """Défaut du POC : 10 euro Nebra 2008 absent des 5 premiers résultats de « 10 Euro » + 2008."""
    extraction = {
        **EXTRACTION, "country": "Germany", "denomination_text": "10 Euro", "currency": "euro",
        "year": 2008, "is_commemorative_guess": True,
        "search_keywords": ["10 Euro", "Germany", "Nebra", "sky disc"],
    }
    assert matching.design_keywords(extraction, "germany") == ["Nebra", "sky disc"]
    queries = matching.build_queries(extraction, "germany", 147)
    assert queries[0]["q"] == "10 Euro Nebra"
    assert queries[0]["date"] == "2008"
    assert queries[1]["q"] == "10 Euro"


def test_no_denomination_uses_keywords():
    extraction = {**EXTRACTION, "denomination_text": None, "search_keywords": ["Hadrian", "denarius"]}
    assert matching.build_queries(extraction, None, None) == [
        {"issuer": None, "object_type": None, "q": "Hadrian denarius"}
    ]


def test_find_candidates_stops_at_first_hit(numista_client, fake_numista):
    calls = []

    def types(params):
        calls.append(params)
        if "date" in params:
            return {"count": 0, "types": []}  # première requête vide -> on élargit
        return {"count": 1, "types": [{"id": 5, "title": "2 Euro", "issuer": {"name": "France"}}]}

    fake_numista.types_handler = types
    index = asyncio.run(matching.load_issuer_index(numista_client))
    type_ids = asyncio.run(matching.object_type_ids(numista_client))
    assert type_ids == {"coin": 147, "banknote": 18, "token": 3}
    result = asyncio.run(matching.find_candidates(numista_client, index, EXTRACTION, type_ids))
    assert [c.id for c in result.candidates] == [5]
    assert [q["results"] for q in result.queries] == [0, 1]
    assert len(calls) == 2


# --- vision (Ollama simulé) ------------------------------------------------------------------------


def identify(fake_ollama, tmp_path, jpeg, n=2):
    paths = []
    for i in range(n):
        path = tmp_path / f"{i}.jpg"
        path.write_bytes(jpeg)
        paths.append(path)
    return asyncio.run(vision.identify(
        paths, ollama_url=OLLAMA_URL, model="test-vision:1b", transport=fake_ollama.transport
    ))


def test_vision_payload(fake_ollama, tmp_path, jpeg):
    result = identify(fake_ollama, tmp_path, jpeg)
    assert result.extraction == EXTRACTION
    payload = fake_ollama.chat_payloads[0]
    assert len(payload["messages"][0]["images"]) == 2
    assert "obverse" in payload["messages"][0]["content"]
    assert payload["format"] == vision.EXTRACTION_SCHEMA
    assert payload["options"]["temperature"] == 0
    # Défaut du POC : réponse sans limite -> JSON tronqué
    assert payload["options"]["num_predict"] == vision.MAX_OUTPUT_TOKENS
    assert vision.EXTRACTION_SCHEMA["properties"]["inscriptions"]["maxItems"] == vision.MAX_INSCRIPTIONS
    assert "think" not in payload


def test_vision_disables_thinking(fake_ollama, tmp_path, jpeg):
    fake_ollama.capabilities = ["completion", "vision", "thinking"]
    identify(fake_ollama, tmp_path, jpeg, n=1)
    assert fake_ollama.chat_payloads[0]["think"] is False


def test_vision_truncated_answer(fake_ollama, tmp_path, jpeg):
    fake_ollama.done_reason = "length"
    with pytest.raises(vision.VisionError, match="tronquée"):
        identify(fake_ollama, tmp_path, jpeg)


def test_vision_not_json(fake_ollama, tmp_path, jpeg):
    fake_ollama.raw_content = "Je ne sais pas"
    with pytest.raises(vision.VisionError, match="non JSON"):
        identify(fake_ollama, tmp_path, jpeg)


def test_vision_model_without_vision(fake_ollama, tmp_path, jpeg):
    fake_ollama.capabilities = ["completion"]
    with pytest.raises(vision.VisionError, match="ne lit pas les images"):
        identify(fake_ollama, tmp_path, jpeg)


def test_vision_ollama_down(fake_ollama, tmp_path, jpeg):
    fake_ollama.down = True
    with pytest.raises(vision.VisionError, match="injoignable"):
        identify(fake_ollama, tmp_path, jpeg)


def test_prepare_image_resizes(tmp_path):
    from .conftest import make_image

    path = tmp_path / "big.png"
    path.write_bytes(make_image(size=(3000, 1500), fmt="PNG"))
    import base64
    import io

    from PIL import Image

    with Image.open(io.BytesIO(base64.b64decode(vision.prepare_image(path)))) as img:
        assert img.format == "JPEG"
        assert max(img.size) == vision.MAX_SIDE
