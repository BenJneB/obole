"""Phase 4 : recadrage automatique, re-classement visuel des candidats, synchronisation Numista."""

import asyncio
import base64
import io
import json

import pytest
from PIL import Image, ImageDraw

from app import storage
from app.services import collection_sync as sync
from app.services import crop, rerank, vision

from .conftest import OLLAMA_URL, create, identify, make_image

# --- recadrage ---------------------------------------------------------------------------------


def scene(
    size=(1200, 1600), background=(225, 215, 195), coin_box=(400, 600, 760, 960), gradient=40
) -> Image.Image:
    """Photo « de téléphone » : pièce ronde sur un fond uni avec un dégradé de lumière."""
    shade = Image.linear_gradient("L").resize(size).point(lambda v: v * gradient // 255)
    dark = Image.new("RGB", size, tuple(max(0, c - gradient) for c in background))
    img = Image.composite(dark, Image.new("RGB", size, background), shade)
    draw = ImageDraw.Draw(img)
    draw.ellipse(coin_box, fill=(120, 110, 80))
    x0, y0, x1, y1 = coin_box
    draw.ellipse((x0 + 60, y0 + 60, x1 - 60, y1 - 60), fill=(170, 160, 120))  # motif central
    return img


def test_crop_finds_coin_on_plain_background():
    box = crop.find_object_box(scene())
    assert box is not None
    x0, y0, x1, y1 = box
    # La pièce entière est dans le cadre, avec une marge raisonnable (pas toute la photo)
    assert x0 <= 400 and y0 <= 600 and x1 >= 760 and y1 >= 960
    assert x0 >= 300 and y0 >= 500 and x1 <= 860 and y1 <= 1060


def test_crop_keeps_two_coins_side_by_side():
    img = scene(coin_box=(100, 600, 460, 960))
    ImageDraw.Draw(img).ellipse((700, 620, 1040, 960), fill=(90, 90, 100))
    x0, _, x1, _ = crop.find_object_box(img)
    assert x0 <= 100 and x1 >= 1040


@pytest.mark.parametrize("img", [
    Image.new("RGB", (800, 600), (200, 150, 50)),  # rien à recadrer
    scene(coin_box=(-50, -50, 1250, 1650)),  # objet qui remplit déjà la photo
    Image.effect_noise((80, 60), 120).resize((800, 600), Image.NEAREST).convert("RGB"),  # fond chargé
], ids=["uniform", "already-framed", "busy-background"])
def test_crop_abstains_when_unsure(img):
    assert crop.find_object_box(img) is None
    assert crop.autocrop(img) is img


def test_thumbnail_and_model_image_are_cropped_but_original_kept(tmp_path):
    source = tmp_path / "photo.jpg"
    scene().save(source, "JPEG")
    original = source.read_bytes()

    storage.make_thumbnail(source, tmp_path / "plain.jpg")
    storage.make_thumbnail(source, tmp_path / "cropped.jpg", auto_crop=True)
    with Image.open(tmp_path / "plain.jpg") as plain, Image.open(tmp_path / "cropped.jpg") as cropped:
        assert plain.size == (300, 400)  # photo entière, portrait
        assert abs(cropped.width - cropped.height) < 20  # cadre serré autour de la pièce ronde
    assert source.read_bytes() == original

    with Image.open(io.BytesIO(base64.b64decode(vision.prepare_image(source, auto_crop=True)))) as img:
        assert abs(img.width - img.height) < 40 and max(img.size) <= vision.MAX_SIDE


def test_item_photo_thumbnail_uses_auto_crop_setting(client, data_dir):
    buffer = io.BytesIO()
    scene().save(buffer, "JPEG")
    item = create(client)
    photo = client.post(
        f"/api/items/{item['id']}/photos", files={"file": ("p.jpg", buffer.getvalue(), "image/jpeg")}
    ).json()
    thumb = client.get(photo["thumb_url"])
    with Image.open(io.BytesIO(thumb.content)) as img:
        assert abs(img.width - img.height) < 20
    full = client.get(photo["url"])
    assert full.content == buffer.getvalue()  # original intact


# --- re-classement ---------------------------------------------------------------------------------

THUMB = "https://en.numista.com/catalogue/photos/france/{}-180.jpg"


def candidates_body(ids=(1001, 1002, 1003)):
    return [
        {
            "id": i, "title": f"Type {i}", "issuer": "France", "years": "2001", "url": f"https://fr.numista.com/{i}",
            "obverse_thumbnail": THUMB.format(f"{i}a"), "reverse_thumbnail": THUMB.format(f"{i}r"),
        }
        for i in ids
    ]


@pytest.fixture
def thumbnails(fake_web):
    for i in (1001, 1002, 1003):
        for side in "ar":
            path = f"/catalogue/photos/france/{i}{side}-180.jpg"
            fake_web.images[path] = make_image((i % 255, 80, 40), (180, 180))
    return fake_web


def answer(fake_ollama, best, confidence=0.8, reason="Même portrait"):
    fake_ollama.raw_content = json.dumps({"best": best, "confidence": confidence, "reason": reason})


def test_identify_announces_rerank(client, jpeg):
    assert identify(client, jpeg)["rerank"] is True


def test_rerank_puts_model_choice_first(client, jpeg, fake_ollama, thumbnails, fake_numista):
    upload_id = identify(client, jpeg)["upload_id"]
    numista_calls = len(fake_numista.requests)
    answer(fake_ollama, 2)
    response = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body()})
    assert response.status_code == 200, response.text
    body = response.json()
    # « 2 » dans l'ordre (1001, 1002, 1003) puis dans l'ordre inverse : 1002 les deux fois
    assert body["verdict"] == "match"
    assert body["order"] == [1002, 1001, 1003]
    assert body["best_id"] == 1002
    assert body["confidence"] == 0.8
    assert body["missing_thumbnails"] == 0

    first, payload = fake_ollama.chat_payloads[-2:]  # deux passes : ordre de Numista puis inverse
    assert first["messages"][0]["images"][1] != payload["messages"][0]["images"][1]
    assert first["messages"][0]["images"][0] == payload["messages"][0]["images"][0]
    assert len(payload["messages"][0]["images"]) == 2  # photo + planche, quel que soit le nombre de fiches
    assert payload["format"]["properties"]["best"]["enum"] == [0, 1, 2, 3]
    with Image.open(io.BytesIO(base64.b64decode(payload["messages"][0]["images"][1]))) as sheet:
        assert max(sheet.size) <= vision.MAX_SIDE and sheet.height > sheet.width  # une ligne par fiche
    assert len(fake_numista.requests) == numista_calls  # aucun quota Numista consommé

    # Miniatures en cache disque : un second re-classement ne retélécharge rien
    downloads = len(thumbnails.requests)
    client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body()})
    assert len(thumbnails.requests) == downloads


def test_rerank_none_matches_keeps_order(client, jpeg, fake_ollama, thumbnails):
    upload_id = identify(client, jpeg)["upload_id"]
    answer(fake_ollama, 0, confidence=1.7)
    body = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body()}).json()
    assert body["verdict"] == "none"
    assert body["best_id"] is None
    assert body["order"] == [1001, 1002, 1003]
    assert body["confidence"] == 1.0  # borné


def test_rerank_position_bias_is_not_trusted(client, jpeg, fake_ollama, thumbnails):
    """« Toujours la ligne 1 » : 1001 dans l'ordre, 1003 dans l'ordre inverse -> pas de choix."""
    upload_id = identify(client, jpeg)["upload_id"]
    answer(fake_ollama, 1, confidence=0.99)
    body = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body()}).json()
    assert body["verdict"] == "unsure"
    assert body["best_id"] is None
    assert body["order"] == [1001, 1002, 1003]  # ordre de Numista conservé
    assert body["confidence"] == 0.0


def test_rerank_never_fetches_foreign_hosts(client, jpeg, fake_ollama, fake_web):
    upload_id = identify(client, jpeg)["upload_id"]
    hostile = candidates_body((1, 2))
    hostile[0]["obverse_thumbnail"] = "http://169.254.169.254/latest/meta-data"
    hostile[1]["obverse_thumbnail"] = "https://numista.com.evil.example/x.jpg"
    hostile[1]["reverse_thumbnail"] = "file:///etc/passwd"
    hostile[0]["reverse_thumbnail"] = None
    response = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": hostile})
    assert response.status_code == 502
    assert "Miniatures Numista indisponibles" in response.json()["detail"]
    assert fake_web.requests == []


def test_rerank_errors(client, jpeg, fake_ollama, thumbnails):
    upload_id = identify(client, jpeg)["upload_id"]
    one = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body((1001,))})
    assert one.status_code == 422
    expired = client.post("/api/rerank", json={"upload_id": "0" * 32, "candidates": candidates_body()})
    assert expired.status_code == 400
    fake_ollama.down = True
    down = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body()})
    assert down.status_code == 502 and "Re-classement impossible" in down.json()["detail"]


def test_rerank_disabled(client, jpeg, settings):
    upload_id = identify(client, jpeg)["upload_id"]
    settings.rerank = False
    response = client.post("/api/rerank", json={"upload_id": upload_id, "candidates": candidates_body()})
    assert response.status_code == 409


def test_candidate_sheet_marks_missing_pictures():
    sheet = rerank.candidate_sheet([(None, None), (Image.new("RGB", (180, 180), "red"), None)])
    assert sheet.size == (rerank.LABEL_WIDTH + 2 * 180 + 2 * rerank.GAP, 2 * 180 + 3 * rerank.GAP)
    grey = sheet.getpixel((rerank.LABEL_WIDTH + 90, rerank.GAP + 90))
    red = sheet.getpixel((rerank.LABEL_WIDTH + 90, 2 * rerank.GAP + 180 + 90))
    assert grey == (200, 200, 200) and red[0] > 200 and red[1] < 50


def test_rerank_rejects_out_of_range_answer(tmp_path, fake_ollama, fake_web):
    photo = tmp_path / "a.jpg"
    photo.write_bytes(make_image())
    fake_web.images["/x.jpg"] = make_image(size=(180, 180))
    fake_ollama.raw_content = json.dumps({"best": 9, "confidence": 0.5, "reason": ""})
    cands = [rerank.RerankCandidate(i, "https://en.numista.com/x.jpg", None) for i in (1, 2)]
    with pytest.raises(vision.VisionError):
        asyncio.run(rerank.rerank(
            [photo], cands, ollama_url=OLLAMA_URL, model="m", cache_dir=tmp_path / "cache",
            ollama_transport=fake_ollama.transport, web_transport=fake_web.transport,
        ))


# --- synchronisation avec ma collection Numista -------------------------------------------------------


def collected(type_id, quantity=1, title=None, year=None, grade=None, category="coin"):
    entry = {
        "id": type_id * 10, "quantity": quantity,
        "type": {"id": type_id, "title": title or f"Type {type_id}", "category": category,
                 "issuer": {"code": "france", "name": "France"}},
    }
    if year:
        entry["issue"] = {"id": 1, "year": year, "gregorian_year": year}
    if grade:
        entry["grade"] = grade
    return entry


def test_compare_collections(client, fake_numista):
    create(client, numista_id=1, numista_title="Type 1", quantity=2)  # même quantité
    create(client, numista_id=2, numista_title="Type 2", quantity=3)  # 1 de plus sur Numista
    create(client, numista_id=3, numista_title="Type 3")  # seulement dans Obole
    create(client)  # sans N# : non comparable
    fake_numista.collection = [
        collected(1, 2), collected(2, 2), collected(2, 2), collected(4, 1, "Type 4"), {"id": 5, "type": None},
    ]
    response = client.get("/api/numista/collection/compare")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["user_id"] == 42
    assert [r["numista_id"] for r in body["only_obole"]] == [3]
    assert [r["numista_id"] for r in body["only_numista"]] == [4]
    different = [(r["numista_id"], r["obole_quantity"], r["numista_quantity"]) for r in body["different"]]
    assert different == [(2, 3, 4)]
    assert body["same"] == 1
    assert body["obole_without_numista"] == 1
    assert body["unreadable_entries"] == 1
    assert body["only_numista"][0]["url"] == "https://fr.numista.com/4"

    # Le jeton OAuth est réutilisé : une seconde comparaison ne coûte qu'une requête
    used = body["quota"]["used"]
    again = client.get("/api/numista/collection/compare").json()
    assert again["quota"]["used"] == used + 1
    assert fake_numista.tokens_issued == 1


def test_push_adds_only_the_difference(client, fake_numista):
    create(client, numista_id=3, numista_title="Type 3", quantity=2, condition="TTB")
    create(client, numista_id=3, numista_title="Type 3", quantity=1, condition="ttb")
    create(client, numista_id=5, numista_title="Type 5", quantity=3, condition="SUP")
    create(client, numista_id=5, numista_title="Type 5", quantity=1, condition="TB")
    fake_numista.collection = [collected(5, 1)]

    response = client.post("/api/numista/collection/push", json={"numista_id": 3})
    assert response.status_code == 200, response.text
    assert response.json()["added"] == 3 and response.json()["grade"] == "vf"
    mixed = client.post("/api/numista/collection/push", json={"numista_id": 5}).json()
    assert mixed["added"] == 3 and mixed["grade"] is None  # états différents : pas d'état envoyé
    assert fake_numista.posted == [{"type": 3, "quantity": 3, "grade": "vf"}, {"type": 5, "quantity": 3}]

    # Déjà à jour : rien n'est envoyé une seconde fois
    again = client.post("/api/numista/collection/push", json={"numista_id": 3})
    assert again.status_code == 409
    assert len(fake_numista.posted) == 2


def test_import_creates_missing_copies(client, fake_numista):
    fake_numista.collection = [
        collected(7, 2, "1 Franc Semeuse", year=1960, grade="xf"),
        collected(7, 1, "1 Franc Semeuse", year=1960, grade="xf"),
        collected(8, 1, "10 Euro", category="banknote"),
    ]
    create(client, numista_id=7, numista_title="1 Franc Semeuse", quantity=1)

    response = client.post("/api/numista/collection/import", json={"numista_id": 7})
    assert response.status_code == 201, response.text
    item = response.json()
    assert item["quantity"] == 2
    assert (item["year"], item["condition"]) == (1960, "SUP")
    assert (item["country"], item["issuer_code"]) == ("France", "france")
    assert item["numista_url"] == "https://fr.numista.com/7"
    assert item["identification_source"] == "numista"

    note = client.post("/api/numista/collection/import", json={"numista_id": 8}).json()
    assert note["object_type"] == "banknote" and note["quantity"] == 1

    assert client.post("/api/numista/collection/import", json={"numista_id": 7}).status_code == 409
    assert fake_numista.posted == []  # aucune écriture sur Numista


def test_expired_token_is_renewed_once(client, fake_numista):
    client.get("/api/numista/collection/compare")
    fake_numista.reject_token = "token-1"
    response = client.get("/api/numista/collection/compare")
    assert response.status_code == 200
    assert fake_numista.tokens_issued == 2


def test_collection_access_refused(client, fake_numista):
    fake_numista.deny_collection = True  # tout jeton refusé : droits insuffisants
    response = client.get("/api/numista/collection/compare")
    assert response.status_code == 403
    assert "accès à la collection refusé" in response.json()["detail"]
    assert fake_numista.tokens_issued == 2  # une seule nouvelle tentative, pas de boucle


def test_numista_entry_parsing_is_lenient():
    assert sync.NumistaEntry.parse({"type": {"id": "x"}}) is None
    entry = sync.NumistaEntry.parse({"quantity": 0, "type": {"id": 3, "object_type": {"name": "Banknote"}}})
    assert entry.quantity == 1 and entry.title == "N# 3" and entry.category == "banknote"
    assert sync.grade_of(" t.t.b ") == "vf" and sync.grade_of("bof") is None
