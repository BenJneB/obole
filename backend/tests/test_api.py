"""Routes de l'API, de bout en bout (Ollama et Numista simulés)."""

import csv
import io
import json
import zipfile

from sqlmodel import Session

from app.db import DB_FILENAME, MIGRATIONS, init_db

from .conftest import EXTRACTION, make_image


def identify(client, jpeg, reverse=True):
    files = {"obverse": ("avers.jpg", jpeg, "image/jpeg")}
    if reverse:
        files["reverse"] = ("revers.jpg", make_image((10, 20, 30)), "image/jpeg")
    response = client.post("/api/identify", files=files)
    assert response.status_code == 200, response.text
    return response.json()


def create(client, **fields):
    response = client.post("/api/items", json={"country": "France", "denomination": "2 euro", **fields})
    assert response.status_code == 201, response.text
    return response.json()


# --- identification ------------------------------------------------------------------------------


def test_identify_full_flow(client, jpeg, fake_numista, fake_ollama, data_dir):
    body = identify(client, jpeg)
    assert body["errors"] == []
    assert body["extraction"] == EXTRACTION
    assert body["model"] == "test-vision:1b"
    assert body["sides"] == ["obverse", "reverse"]
    assert body["match"]["issuer_code"] == "france"
    assert [c["id"] for c in body["match"]["candidates"]] == [1001, 1002]
    assert body["match"]["candidates"][0]["url"] == "https://fr.numista.com/1001"
    # issuers en + fr + object_types + 1 recherche
    assert body["quota"]["used"] == 4
    assert (data_dir / "uploads" / body["upload_id"] / "obverse.jpg").is_file()

    # Deuxième identification identique : tout vient du cache, aucun quota consommé
    again = identify(client, jpeg)
    assert again["quota"]["used"] == 4
    assert len(fake_numista.requests) == 4


def test_identify_single_photo(client, jpeg, fake_ollama):
    body = identify(client, jpeg, reverse=False)
    assert body["sides"] == ["obverse"]
    assert len(fake_ollama.chat_payloads[0]["messages"][0]["images"]) == 1


def test_identify_ollama_down_keeps_photos(client, jpeg, fake_ollama, fake_numista):
    fake_ollama.down = True
    body = identify(client, jpeg)
    assert body["extraction"] is None
    assert body["match"] is None
    assert "Analyse de l'image impossible" in body["errors"][0]
    assert fake_numista.requests == []
    # Repli manuel : l'objet peut être créé avec les photos déjà envoyées
    item = create(client, upload_id=body["upload_id"])
    assert len(item["photos"]) == 2


def test_identify_numista_down_keeps_extraction(client, jpeg, fake_numista):
    fake_numista.fail_with = 500
    body = identify(client, jpeg)
    assert body["extraction"]["country"] == "France"
    assert body["match"] is None
    assert "Recherche Numista impossible" in body["errors"][0]


def test_identify_no_candidates(client, jpeg, fake_numista):
    fake_numista.types_handler = lambda params: {"count": 0, "types": []}
    body = identify(client, jpeg)
    assert body["match"]["candidates"] == []
    assert "Aucune fiche" in body["errors"][0]
    assert len(body["match"]["queries"]) == 3


def test_identify_rejects_non_image(client):
    response = client.post("/api/identify", files={"obverse": ("x.jpg", b"pas une image", "image/jpeg")})
    assert response.status_code == 400
    assert "illisible" in response.json()["detail"]


# --- collection ------------------------------------------------------------------------------------


def test_create_from_identification(client, jpeg, data_dir):
    body = identify(client, jpeg)
    item = create(
        client, upload_id=body["upload_id"], ai_extraction=body["extraction"], numista_id=1001,
        numista_title="2 Euro (Arbre)", identification_source="ai_numista", condition="SUP", quantity=2,
        estimated_value=2.5,
    )
    assert item["numista_url"] == "https://fr.numista.com/1001"
    assert item["ai_extraction"] == EXTRACTION
    assert [p["side"] for p in item["photos"]] == ["obverse", "reverse"]
    # Les photos ont quitté le dossier d'attente
    assert not (data_dir / "uploads" / body["upload_id"]).exists()
    photo = item["photos"][0]
    original = client.get(photo["url"])
    assert original.status_code == 200
    assert original.content == jpeg  # original conservé à l'identique
    thumb = client.get(photo["thumb_url"])
    assert thumb.status_code == 200
    assert thumb.headers["content-type"] == "image/jpeg"
    # Un envoi ne peut servir qu'une fois
    response = client.post("/api/items", json={"upload_id": body["upload_id"]})
    assert response.status_code == 400


def test_create_with_unknown_upload(client):
    response = client.post("/api/items", json={"upload_id": "../../etc"})
    assert response.status_code == 400
    response = client.post("/api/items", json={"upload_id": "0" * 32})
    assert response.status_code == 400
    assert client.get("/api/items").json()["total"] == 0  # rien de créé


def test_list_filters_and_sort(client):
    create(client, country="France", year=2001, object_type="coin", notes="trouvée à Lille")
    create(client, country="Germany", denomination="10 euro", year=2008, tags="commémorative")
    create(client, country="United States", denomination="1 dollar", year=1880, object_type="banknote")

    def ids(**params):
        return [i["year"] for i in client.get("/api/items", params=params).json()["items"]]

    assert ids(sort="year") == [1880, 2001, 2008]
    assert ids(sort="-year") == [2008, 2001, 1880]
    assert ids(country="france") == [2001]
    assert ids(object_type="banknote") == [1880]
    assert ids(q="lille") == [2001]
    assert ids(q="commémo") == [2008]
    assert ids(year_min=2000, sort="year") == [2001, 2008]
    assert ids(sort="year", limit=1, offset=1) == [2001]
    assert client.get("/api/items", params={"limit": 1}).json()["total"] == 3
    assert client.get("/api/items", params={"sort": "notes"}).status_code == 422


def test_update_is_partial(client):
    item = create(client, notes="note", quantity=3)
    response = client.put(f"/api/items/{item['id']}", json={"condition": "TTB", "numista_id": 6311})
    assert response.status_code == 200
    updated = response.json()
    assert updated["condition"] == "TTB"
    assert updated["notes"] == "note"  # non envoyé -> inchangé
    assert updated["quantity"] == 3
    assert updated["numista_url"] == "https://fr.numista.com/6311"
    assert updated["updated_at"] >= item["updated_at"]

    cleared = client.patch(f"/api/items/{item['id']}", json={"notes": None}).json()
    assert cleared["notes"] is None
    assert client.put(f"/api/items/{item['id']}", json={"quantity": -1}).status_code == 422
    assert client.put("/api/items/999", json={"notes": "x"}).status_code == 404


def test_delete_item_removes_photos(client, jpeg, data_dir):
    item = create(client, upload_id=identify(client, jpeg)["upload_id"])
    folder = data_dir / "photos" / str(item["id"])
    assert any(folder.iterdir())
    assert client.delete(f"/api/items/{item['id']}").status_code == 204
    assert not folder.exists()
    assert client.get(f"/api/items/{item['id']}").status_code == 404
    assert client.get(item["photos"][0]["url"]).status_code == 404


def test_add_and_delete_photo(client, jpeg):
    item = create(client)
    response = client.post(
        f"/api/items/{item['id']}/photos", files={"file": ("p.png", make_image(fmt="PNG"), "image/png")},
        data={"side": "reverse"},
    )
    assert response.status_code == 201, response.text
    photo = response.json()
    assert photo["side"] == "reverse"
    assert client.get(photo["url"]).status_code == 200
    bad = client.post(f"/api/items/{item['id']}/photos", files={"file": ("p.jpg", b"xx", "image/jpeg")})
    assert bad.status_code == 400
    assert client.delete(f"/api/items/{item['id']}/photos/{photo['id']}").status_code == 204
    assert client.get(f"/api/items/{item['id']}").json()["photos"] == []
    assert client.delete(f"/api/items/{item['id']}/photos/{photo['id']}").status_code == 404


def test_ids_are_never_reused(client):
    first = create(client)
    client.delete(f"/api/items/{first['id']}")
    assert create(client)["id"] != first["id"]


# --- Numista direct ---------------------------------------------------------------------------------


def test_numista_search_and_detail(client, fake_numista):
    response = client.get("/api/numista/search", params={"q": "2 euro", "issuer": "france",
                                                         "object_type": "banknote"})
    assert response.status_code == 200
    assert response.json()["count"] == 2
    search = fake_numista.requests[-1]
    assert search.url.params["object_type"] == "18"
    assert search.url.params["lang"] == "fr"
    detail = client.get("/api/numista/types/6311")
    assert detail.status_code == 200
    assert detail.json()["composition"]["text"] == "Bimétallique"
    assert client.get("/api/numista/types/404").status_code == 404
    assert client.get("/api/numista/search").status_code == 422  # q obligatoire


def test_numista_errors_map_to_http(client, fake_numista, monkeypatch):
    async def no_sleep(_seconds):
        return None

    monkeypatch.setattr("app.services.numista.asyncio.sleep", no_sleep)
    fake_numista.fail_with = 429
    assert client.get("/api/numista/types/1").status_code == 429
    fake_numista.fail_with = 503
    assert client.get("/api/numista/types/2").status_code == 502


# --- export, sauvegarde, état ------------------------------------------------------------------------


def test_export_csv_and_json(client, jpeg):
    create(client, notes="Élégante, « belle » pièce", upload_id=identify(client, jpeg)["upload_id"],
           ai_extraction=EXTRACTION)
    response = client.get("/api/export.csv")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text.lstrip("﻿"))))
    assert rows[0]["notes"] == "Élégante, « belle » pièce"
    assert len(rows[0]["photos"].split()) == 2
    assert "ai_raw_json" not in rows[0]

    data = client.get("/api/export.json").json()
    assert data["items"][0]["ai_extraction"] == EXTRACTION
    assert data["items"][0]["photos"][0].startswith("photos/")


def test_backup_zip(client, jpeg, data_dir):
    item = create(client, upload_id=identify(client, jpeg)["upload_id"])
    response = client.get("/api/backup.zip")
    assert response.status_code == 200
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        assert DB_FILENAME in names
        assert sum(n.startswith(f"photos/{item['id']}/") for n in names) == 4  # 2 photos + 2 miniatures
        restored = data_dir.parent / "restored"
        archive.extract(DB_FILENAME, restored)
    import sqlite3

    conn = sqlite3.connect(restored / DB_FILENAME)
    try:
        assert conn.execute("SELECT count(*) FROM items").fetchone()[0] == 1
    finally:
        conn.close()
    assert not any((data_dir / "tmp").iterdir())  # fichier temporaire supprimé après l'envoi


def test_stats(client):
    create(client, country="France", quantity=2, estimated_value=3.0, year=1998, condition="SUP",
           tags="euro, album 1", acquired_price=5, numista_id=1001)
    create(client, country="france", quantity=1, estimated_value=None, object_type="banknote", year=1998,
           tags="Euro")
    create(client, country="Japan", year=1867, acquired_price=2.5)
    create(client, country="Japan")
    stats = client.get("/api/stats").json()
    assert stats == {
        "items": 4, "total_quantity": 5, "countries": 2,
        "by_type": {"coin": 3, "banknote": 1}, "by_country": {"France": 2, "Japan": 2},
        "by_year": {"1867": 1, "1998": 2}, "without_year": 1,
        "by_condition": {"": 3, "SUP": 1}, "by_tag": {"Euro": 2, "album 1": 1}, "identified": 1,
        "estimated_value_total": 6.0, "acquired_price_total": 7.5,
    }


def test_health(client, fake_ollama, fake_numista):
    body = client.get("/api/health").json()
    assert body["status"] == "ok"
    assert body["ollama"]["ok"] is True
    assert body["numista"] == {"configured": True, "used": 0, "limit": 2000, "remaining": 1980}
    assert body["schema_version"] == len(MIGRATIONS)
    assert fake_numista.requests == []  # jamais de quota consommé par /health

    fake_ollama.down = True
    body = client.get("/api/health").json()
    assert body["status"] == "degraded"
    assert body["ollama"]["ok"] is False
    assert body["ollama"]["error"]


def test_migrations_are_idempotent(client):
    engine = client.app.state.obole.engine
    create(client)
    assert init_db(engine, client.app.state.obole.data_dir) == len(MIGRATIONS)
    with Session(engine) as session:
        assert session.connection().exec_driver_sql("PRAGMA user_version").scalar_one() == len(MIGRATIONS)
    assert client.get("/api/items").json()["total"] == 1


def test_expired_uploads_are_purged(client, jpeg, data_dir):
    from app import storage

    upload_id = identify(client, jpeg)["upload_id"]
    assert storage.purge_old_uploads(data_dir, max_age=-1) == 1
    assert not (data_dir / "uploads" / upload_id).exists()
    assert json.loads(client.post("/api/items", json={"upload_id": upload_id}).text)["detail"].startswith(
        "Photos introuvables"
    )


# --- frontend compilé ---------------------------------------------------------------------------------


def test_frontend_is_served(settings, fake_numista, fake_ollama):
    from fastapi.testclient import TestClient

    from app.factory import create_app

    dist = settings.frontend_dist
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>Obole</html>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (dist / "sw.js").write_text("// sw", encoding="utf-8")
    app = create_app(settings, numista_transport=fake_numista.transport,
                     ollama_transport=fake_ollama.transport)
    with TestClient(app) as client:
        assert client.get("/").text == "<html>Obole</html>"
        assert client.get("/objet/12").text == "<html>Obole</html>"  # route React
        asset = client.get("/assets/app-abc123.js")
        assert "immutable" in asset.headers["cache-control"]
        assert client.get("/sw.js").headers["cache-control"] == "no-cache"
        assert client.get("/manque.png").status_code == 404
        assert client.get("/api/inconnue").status_code == 404
        assert client.get("/api/items").json()["total"] == 0  # l'API n'est pas masquée
        assert client.get("/../../etc/passwd").status_code in (200, 404)
        assert "root:" not in client.get("/..%2F..%2Fetc%2Fpasswd").text
    app.state.obole.engine.dispose()


def test_rematch_with_corrected_reading(client, fake_numista, fake_ollama):
    corrected = {**EXTRACTION, "country": "Allemagne", "denomination_text": "10 Euro", "year": 2008}
    body = client.post("/api/match", json={"extraction": corrected}).json()
    assert body["match"]["issuer_code"] == "germany"
    first_search = next(r for r in fake_numista.requests if r.url.path.endswith("/types"))
    assert first_search.url.params["q"] == "10 Euro"
    assert first_search.url.params["issuer"] == "germany"
    assert fake_ollama.chat_payloads == []  # le modèle n'est pas rappelé
    fake_numista.fail_with = 500
    body = client.post("/api/match", json={"extraction": {**corrected, "year": 1999}}).json()
    assert body["match"] is None
    assert "Recherche Numista impossible" in body["errors"][0]
