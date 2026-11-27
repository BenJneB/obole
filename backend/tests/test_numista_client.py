"""Client Numista : cache SQLite, compteur de quota, erreurs HTTP."""

import asyncio
import json
import time

import httpx
import pytest
from sqlmodel import Session

from app.db import init_db, make_engine
from app.models import ApiUsage
from app.services.numista import QUOTA_SAFETY_MARGIN, NumistaClient, NumistaError, NumistaNotFound, QuotaError

from .conftest import NUMISTA_URL


def run(coro):
    return asyncio.run(coro)


def test_cache_avoids_second_request(numista_client, fake_numista):
    first = run(numista_client.search_types("2 euro", issuer="france"))
    second = run(numista_client.search_types("2 euro", issuer="france"))
    assert first == second
    assert len(fake_numista.requests) == 1
    assert numista_client.used_this_month() == 1


def test_different_params_are_not_shared(numista_client, fake_numista):
    run(numista_client.search_types("2 euro", issuer="france"))
    run(numista_client.search_types("2 euro", issuer="germany"))
    assert len(fake_numista.requests) == 2
    assert numista_client.used_this_month() == 2


def test_none_params_are_not_sent(numista_client, fake_numista):
    run(numista_client.search_types("2 euro"))
    params = dict(fake_numista.requests[0].url.params)
    assert params == {"q": "2 euro", "lang": "en", "count": "5", "order": "relevance"}


def test_quota_blocks_before_network(engine, fake_numista):
    client = NumistaClient("test-key", NUMISTA_URL, engine, monthly_quota=QUOTA_SAFETY_MARGIN + 1,
                           transport=fake_numista.transport)
    run(client.get_type(1))
    with pytest.raises(QuotaError):
        run(client.get_type(2))
    assert len(fake_numista.requests) == 1
    # Le cache reste utilisable quand le quota est atteint
    assert run(client.get_type(1))["id"] == 1


def test_missing_key_uses_cache_only(engine, numista_client, fake_numista):
    run(numista_client.get_type(7))
    no_key = NumistaClient("", NUMISTA_URL, engine, transport=fake_numista.transport)
    assert not no_key.configured
    assert run(no_key.get_type(7))["id"] == 7
    with pytest.raises(NumistaError, match="NUMISTA_API_KEY"):
        run(no_key.get_type(8))


def test_404_and_401(numista_client, fake_numista):
    with pytest.raises(NumistaNotFound):
        run(numista_client.get_type(404))
    fake_numista.fail_with = 401
    with pytest.raises(NumistaError, match="401"):
        run(numista_client.get_type(5))


def test_429_retries_then_raises(numista_client, fake_numista, monkeypatch):
    slept = []

    async def fake_sleep(seconds):
        slept.append(seconds)

    monkeypatch.setattr("app.services.numista.asyncio.sleep", fake_sleep)
    fake_numista.fail_with = 429
    with pytest.raises(QuotaError, match="429"):
        run(numista_client.get_type(5))
    assert len(fake_numista.requests) == 2  # une seule nouvelle tentative
    assert slept == [5]
    assert numista_client.used_this_month() == 2  # chaque envoi compte


def test_errors_are_not_cached(numista_client, fake_numista):
    fake_numista.fail_with = 500
    with pytest.raises(NumistaError):
        run(numista_client.get_type(5))
    fake_numista.fail_with = None
    assert run(numista_client.get_type(5))["id"] == 5


def test_poc_cache_is_imported(tmp_path):
    """Migration 2 : le cache fichier du POC est repris, la même requête ne consomme pas de quota."""
    data_dir = tmp_path / "data"
    cache_dir = data_dir / "cache" / "numista"
    cache_dir.mkdir(parents=True)
    params = {"lang": "en"}
    (cache_dir / "abc.json").write_text(json.dumps({
        "path": "/types/42", "params": params, "fetched_at": time.time() - 3600,
        "response": {"id": 42, "title": "Repris du POC"},
    }), encoding="utf-8")
    (cache_dir / "broken.json").write_text("{pas du json", encoding="utf-8")
    (data_dir / "cache" / "numista_usage.json").write_text('{"2026-11": 54}', encoding="utf-8")

    engine = make_engine(data_dir)
    init_db(engine, data_dir)
    def no_network(request):
        raise AssertionError(f"requête inattendue : {request.url}")

    client = NumistaClient("test-key", NUMISTA_URL, engine, transport=httpx.MockTransport(no_network))
    assert run(client.get_type(42))["title"] == "Repris du POC"
    with Session(engine) as session:
        assert session.get(ApiUsage, "2026-11").numista_requests == 54
    engine.dispose()
