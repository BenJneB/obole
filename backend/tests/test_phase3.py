"""Phase 3 : tags, filtres avancés, doublons, sauvegarde automatique."""

import time
import zipfile

import pytest
from fastapi.testclient import TestClient

from app import backup
from app.db import DB_FILENAME, MIGRATIONS, init_db, make_engine
from app.factory import create_app
from app.models import normalize_tags

from .conftest import create, identify

# --- tags ------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, None),
        ("", None),
        (" , ,", None),
        ("euro", "euro"),
        ("  Pays-Bas,album   2 ,Album 2, euro,", "Pays-Bas, album 2, euro"),
    ],
)
def test_normalize_tags(raw, expected):
    assert normalize_tags(raw) == expected


def test_tags_are_normalized_and_filterable(client):
    a = create(client, tags="Euro,  album 2 ,euro")
    assert a["tags"] == "Euro, album 2"
    b = create(client, tags="album 2, 100%_rare")
    create(client, tags="album 21")
    create(client)

    def ids(**params):
        return [i["id"] for i in client.get("/api/items", params=params).json()["items"]]

    assert ids(tag="ALBUM 2") == [b["id"], a["id"]]  # exact, sans casse : pas « album 21 »
    assert ids(tag="100%_rare") == [b["id"]]  # % et _ ne sont pas des jokers
    assert ids(tag="album") == []

    updated = client.patch(f"/api/items/{a['id']}", json={"tags": " , "}).json()
    assert updated["tags"] is None

    tags = client.get("/api/tags").json()
    assert tags == [
        {"tag": "100%_rare", "count": 1}, {"tag": "album 2", "count": 1}, {"tag": "album 21", "count": 1},
    ]


def test_tag_counts_are_case_insensitive(client):
    create(client, tags="euro")
    create(client, tags="Euro, rare")
    assert client.get("/api/tags").json() == [{"tag": "Euro", "count": 2}, {"tag": "rare", "count": 1}]


def test_migration_normalizes_existing_tags(data_dir):
    engine = make_engine(data_dir)
    try:
        init_db(engine, data_dir)
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO items"
                " (object_type, quantity, identification_source, tags, created_at, updated_at)"
                " VALUES ('coin', 1, 'manual', 'a,b ,A', '2026-01-01', '2026-01-01')"
            )
            conn.exec_driver_sql("PRAGMA user_version = 2")  # base de la Phase 2
        assert init_db(engine, data_dir) == len(MIGRATIONS)
        with engine.connect() as conn:
            assert conn.exec_driver_sql("SELECT tags FROM items").scalar_one() == "a, b"
    finally:
        engine.dispose()


# --- filtres avancés ---------------------------------------------------------------------------------


def test_advanced_filters(client):
    old = create(client, year=1890, condition="TB", storage_location="Album 1, page 3", numista_id=1001)
    new = create(client, year=2002, condition="SUP", storage_location="Boîte bleue")
    bare = create(client)

    def ids(**params):
        response = client.get("/api/items", params=params)
        assert response.status_code == 200, response.text
        return sorted(i["id"] for i in response.json()["items"])

    assert ids(year_min=1900) == [new["id"]]
    assert ids(year_max=1900) == [old["id"]]
    assert ids(condition="SUP") == [new["id"]]
    assert ids(condition="none") == [bare["id"]]
    assert ids(storage="album") == [old["id"]]
    assert ids(identified="true") == [old["id"]]
    assert ids(identified="false") == [new["id"], bare["id"]]
    assert ids(identified="false", year_min=2000) == [new["id"]]


# --- doublons --------------------------------------------------------------------------------------


def test_owned_lists_items_by_numista_id(client, jpeg):
    first = create(client, numista_id=1001, year=2012, upload_id=identify(client, jpeg)["upload_id"])
    second = create(client, numista_id=1001, year=2005, condition="TTB")
    create(client, numista_id=1002)
    create(client)

    owned = client.get("/api/items/owned", params={"numista_ids": "1001, 1003"}).json()
    assert list(owned) == ["1001"]
    assert [o["id"] for o in owned["1001"]] == [second["id"], first["id"]]  # par année
    assert owned["1001"][1]["thumb_url"] == first["photos"][0]["thumb_url"]  # avers
    assert owned["1001"][0]["thumb_url"] is None

    assert client.get("/api/items/owned", params={"numista_ids": ""}).json() == {}
    assert client.get("/api/items/owned", params={"numista_ids": "x"}).status_code == 422


def test_increment_adds_a_copy_and_discards_photos(client, jpeg, data_dir):
    item = create(client, numista_id=1001, quantity=2)
    upload_id = identify(client, jpeg)["upload_id"]
    response = client.post(f"/api/items/{item['id']}/increment", json={"discard_upload_id": upload_id})
    assert response.status_code == 200, response.text
    assert response.json()["quantity"] == 3
    assert response.json()["updated_at"] > item["updated_at"]
    assert not (data_dir / "uploads" / upload_id).exists()

    assert client.post(f"/api/items/{item['id']}/increment", json={"by": 0}).status_code == 422
    assert client.post("/api/items/999/increment", json={}).status_code == 404


# --- sauvegarde automatique ---------------------------------------------------------------------


def test_auto_backup_only_when_changed_and_rotates(client, data_dir, tmp_path, monkeypatch):
    engine = client.app.state.obole.engine
    target = tmp_path / "sauvegardes"
    names = iter(f"obole-auto-2026010{i}-000000.zip" for i in range(1, 10))
    monkeypatch.setattr(backup, "auto_backup_name", lambda: next(names))

    assert backup.run_auto_backup(data_dir, target, engine, keep=2) == "created"
    assert backup.run_auto_backup(data_dir, target, engine, keep=2) == "unchanged"
    item = create(client)
    assert backup.run_auto_backup(data_dir, target, engine, keep=2) == "created"
    client.patch(f"/api/items/{item['id']}", json={"notes": "modifié"})
    assert backup.run_auto_backup(data_dir, target, engine, keep=2) == "created"
    assert backup.run_auto_backup(data_dir, target, engine, keep=2, force=True) == "created"

    kept = sorted(p.name for p in target.glob("*.zip"))
    assert kept == ["obole-auto-20260103-000000.zip", "obole-auto-20260104-000000.zip"]
    assert not list(target.glob("*.part"))
    with zipfile.ZipFile(target / kept[-1]) as archive:
        assert DB_FILENAME in archive.namelist()
    assert backup.last_run(target)["status"] == "created"


def test_backup_is_due(tmp_path):
    folder = tmp_path / "b"
    assert backup.is_due(folder, 24)
    folder.mkdir()
    backup._write_state(folder, {"last_run": {"at": time.time(), "status": "unchanged"}})
    assert not backup.is_due(folder, 24)
    backup._write_state(folder, {"last_run": {"at": time.time() - 25 * 3600, "status": "created"}})
    assert backup.is_due(folder, 24)
    backup.record_error(folder, "disque plein")
    assert backup.is_due(folder, 24)  # un échec est retenté sans attendre l'intervalle


def test_scheduler_backs_up_at_startup(settings, fake_numista, fake_ollama, tmp_path):
    settings.backup_interval_hours = 24
    settings.backup_dir = tmp_path / "auto"
    app = create_app(
        settings, numista_transport=fake_numista.transport, ollama_transport=fake_ollama.transport
    )
    try:
        with TestClient(app) as client:
            deadline = time.time() + 10
            while not list((tmp_path / "auto").glob(f"{backup.AUTO_PREFIX}*.zip")) and time.time() < deadline:
                time.sleep(0.05)
            status = client.get("/api/backups").json()
            assert status["enabled"] is True
            assert status["last_status"] == "created"
            assert len(status["backups"]) == 1
    finally:
        app.state.obole.engine.dispose()


def test_backup_now_endpoint(client, settings):
    status = client.get("/api/backups").json()
    assert status["enabled"] is False  # désactivée dans les tests
    assert status["backups"] == []
    assert status["directory"] == str(settings.data_path / "backups")

    status = client.post("/api/backups").json()
    assert status["last_status"] == "created"
    assert len(status["backups"]) == 1
    assert status["backups"][0]["size"] > 0


# --- simplifications ---------------------------------------------------------------------------


def test_accented_filters_ignore_case(client):
    egypt = create(client, country="Égypte", tags="Été, rare")
    create(client, country="France")
    for params in ({"country": "égypte"}, {"tag": "été"}, {"q": "ÉGYP"}):
        assert [i["id"] for i in client.get("/api/items", params=params).json()["items"]] == [egypt["id"]]
    create(client, country="égypte")
    assert client.get("/api/stats").json()["countries"] == 2


def test_changing_numista_id_clears_title_and_recomputes_url(client):
    item = create(client, numista_id=1001, numista_title="2 Euro (Arbre)", numista_url="https://ailleurs")
    assert item["numista_url"] == "https://fr.numista.com/1001"  # toujours déduite du N#
    updated = client.patch(f"/api/items/{item['id']}", json={"numista_id": 1002}).json()
    assert updated["numista_title"] is None
    assert updated["numista_url"] == "https://fr.numista.com/1002"
    same = client.patch(f"/api/items/{updated['id']}", json={"numista_id": 1002, "notes": "x"}).json()
    assert same["numista_title"] is None and same["notes"] == "x"
    kept = client.patch(f"/api/items/{item['id']}", json={"numista_id": 1001, "numista_title": "T"}).json()
    assert kept["numista_title"] == "T"
    cleared = client.patch(f"/api/items/{item['id']}", json={"numista_id": None}).json()
    assert cleared["numista_url"] is None and cleared["numista_title"] is None


def test_item_thumb_prefers_obverse(client, jpeg):
    item = create(client)
    assert item["thumb_url"] is None
    other = client.post(f"/api/items/{item['id']}/photos", files={"file": ("a.jpg", jpeg, "image/jpeg")},
                        data={"side": "other"}).json()
    front = client.post(f"/api/items/{item['id']}/photos", files={"file": ("b.jpg", jpeg, "image/jpeg")},
                        data={"side": "obverse"}).json()
    assert other["id"] < front["id"]
    assert client.get(f"/api/items/{item['id']}").json()["thumb_url"] == front["thumb_url"]
    bad = client.post(f"/api/items/{item['id']}/photos",
                      files={"file": ("c.jpg", b"pas une image", "image/jpeg")})
    assert bad.status_code == 400  # PhotoError convertie par le gestionnaire global
