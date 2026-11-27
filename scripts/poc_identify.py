"""Obole — POC d'identification (Phase 0), en ligne de commande.

Lancer depuis la racine du dépôt :

  # Identifier un objet (1 ou 2 photos)
  uv run --project backend python scripts/poc_identify.py photo_avers.jpg [photo_revers.jpg]

  # Recherche manuelle dans Numista (repli quand l'IA échoue)
  uv run --project backend python scripts/poc_identify.py --search "20 kopecks" --issuer russie

  # Banc d'essai sur data/test-photos (attendu.csv), en comparant plusieurs modèles
  uv run --project backend python scripts/poc_identify.py --bench --models gemma4:e4b,qwen2.5vl:7b

Options : --model, --num-ctx, --no-numista (extraction seule), --json (sortie brute).
Ce script ne touche pas à la collection : il lit les photos et n'écrit que dans le cache Numista
(base SQLite de DATA_DIR, partagé avec le serveur) et data/poc-results.
"""

import argparse
import asyncio
import csv
import json
import statistics
import sys
from datetime import datetime
from pathlib import Path

# Rendre le paquet "app" (backend/app) importable depuis scripts/
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.db import init_db, make_engine  # noqa: E402
from app.services import matching, vision  # noqa: E402
from app.services.numista import NumistaClient, NumistaError  # noqa: E402

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # console Windows


def numista_client() -> NumistaClient:
    s = get_settings()
    engine = make_engine(s.data_path)
    init_db(engine, s.data_path)
    return NumistaClient(
        s.numista_api_key.get_secret_value(), s.numista_base_url, engine, s.numista_monthly_quota
    )


def print_extraction(result: vision.VisionResult) -> None:
    e = result.extraction
    print(f"\n=== Lecture par le modèle ({result.model}) ===")
    print(f"  Type          : {e.get('object_type')}")
    print(f"  Pays          : {e.get('country')}  (confiance {e.get('country_confidence')})")
    print(f"  Valeur        : {e.get('denomination_text')}  [{e.get('currency')}]")
    print(f"  Année         : {e.get('year')}  (visible : {e.get('year_visible')})")
    print(f"  Inscriptions  : {' | '.join(e.get('inscriptions') or [])}")
    print(f"  Description   : {e.get('description')}")
    print(f"  Commémorative : {e.get('is_commemorative_guess')}")
    print(f"  Mots-clés     : {', '.join(e.get('search_keywords') or [])}")
    load = f", dont chargement {result.load_seconds:.1f} s" if result.load_seconds else ""
    print(f"  Temps         : {result.seconds:.1f} s{load} — tokens entrée {result.prompt_tokens}, "
          f"sortie {result.output_tokens}")


def print_candidates(match: matching.MatchResult) -> None:
    print("\n=== Numista ===")
    if match.issuer_code:
        print(f"  Émetteur : {match.issuer_name} -> {match.issuer_code} (score {match.issuer_score:.0f})")
    else:
        print("  Émetteur : non reconnu (recherche sans filtre de pays)")
    for q in match.queries:
        shown = {k: v for k, v in q.items() if v is not None and k != "results"}
        print(f"  Requête  : {shown} -> {q['results']} résultat(s)")
    if not match.candidates:
        print("  Aucun candidat. Repli : --search \"...\" pour une recherche manuelle, ou saisie à la main.")
        return
    print(f"\n  {'#':<2} {'N#':>7}  {'Années':<10} Titre")
    for i, c in enumerate(match.candidates, 1):
        print(f"  {i:<2} {c.id:>7}  {c.years:<10} {c.title}  ({c.issuer})")
        print(f"     {'':>7}  {c.url}")


async def identify_one(images: list[Path], model: str, num_ctx: int, use_numista: bool):
    s = get_settings()
    result = await vision.identify(images, ollama_url=s.ollama_url, model=model, num_ctx=num_ctx)
    match = None
    if use_numista:
        client = numista_client()
        index = await matching.load_issuer_index(client)
        type_ids = await matching.object_type_ids(client)
        match = await matching.find_candidates(client, index, result.extraction, type_ids)
    return result, match


async def cmd_identify(args) -> int:
    images = [Path(p) for p in args.photos]
    for p in images:
        if not p.is_file():
            print(f"Photo introuvable : {p}", file=sys.stderr)
            return 2
    try:
        result, match = await identify_one(images, args.model, args.num_ctx, not args.no_numista)
    except (vision.VisionError, NumistaError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result.extraction, ensure_ascii=False, indent=2))
    else:
        print_extraction(result)
    if match:
        print_candidates(match)
    return 0


async def cmd_search(args) -> int:
    client = numista_client()
    object_type = (await matching.object_type_ids(client)).get(args.type) if args.type else None
    data = await client.search_types(args.search, issuer=args.issuer, date=args.year, count=args.count,
                                     object_type=object_type)
    print(f"{data.get('count', 0)} résultat(s) pour {args.search!r}")
    for t in data.get("types", []):
        years = f"{t.get('min_year')}-{t.get('max_year')}"
        print(f"  {t['id']:>7}  {years:<10} {t['title']}  ({(t.get('issuer') or {}).get('name')})")
    print(f"(requêtes Numista ce mois-ci : {client.used_this_month()})")
    return 0


async def cmd_bench(args) -> int:
    s = get_settings()
    photos_dir = Path(args.dir)
    expected_csv = photos_dir / "attendu.csv"
    if not expected_csv.is_file():
        print(f"Fichier introuvable : {expected_csv}", file=sys.stderr)
        return 2
    with expected_csv.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    client = numista_client()
    index = await matching.load_issuer_index(client)
    type_ids = await matching.object_type_ids(client)
    models = [m.strip() for m in args.models.split(",") if m.strip()]
    out_dir = s.data_path / "poc-results"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"bench-{datetime.now():%Y%m%d-%H%M%S}.csv"
    records = []
    fields = ["model", "id", "description", "expected", "rank", "seconds", "prompt_tokens", "country",
              "issuer_code", "denomination", "year", "candidates", "queries", "error"]
    # Écriture au fil de l'eau : un arrêt en cours de route ne perd pas les résultats déjà obtenus
    out = out_file.open("w", encoding="utf-8", newline="")
    writer = csv.DictWriter(out, fieldnames=fields, restval="")
    writer.writeheader()

    def save(record: dict) -> None:
        records.append(record)
        writer.writerow(record)
        out.flush()

    for model in models:
        print(f"\n##### Modèle {model} #####")
        # Préchauffage : le premier appel charge le modèle en VRAM, ne pas le compter dans les temps
        first = photos_dir / rows[0]["photos"].split()[0]
        try:
            await vision.identify([first], ollama_url=s.ollama_url, model=model, num_ctx=args.num_ctx)
        except vision.VisionError as exc:
            print(f"  Modèle indisponible : {exc}")
            continue
        for row in rows:
            images = [photos_dir / name for name in row["photos"].split()]
            expected = int(row["numista_id"]) if row.get("numista_id", "").strip() else None
            try:
                result = await vision.identify(images, ollama_url=s.ollama_url, model=model,
                                               num_ctx=args.num_ctx)
                match = await matching.find_candidates(client, index, result.extraction, type_ids)
            except (vision.VisionError, NumistaError) as exc:
                print(f"  {row['id']} ERREUR : {exc}")
                save({"model": model, "id": row["id"], "error": str(exc)})
                continue
            ids = [c.id for c in match.candidates]
            rank = ids.index(expected) + 1 if expected in ids else None
            verdict = "?" if expected is None else (f"OK (rang {rank})" if rank else "RATÉ")
            e = result.extraction
            print(f"  {row['id']} {verdict:<12} {result.seconds:5.1f} s  "
                  f"lu: {e.get('country')} / {e.get('denomination_text')} / {e.get('year')}  "
                  f"-> {ids[:5]}  ({row['description']})")
            save({
                "model": model, "id": row["id"], "description": row["description"],
                "expected": expected, "rank": rank, "seconds": round(result.seconds, 1),
                "prompt_tokens": result.prompt_tokens, "country": e.get("country"),
                "issuer_code": match.issuer_code, "denomination": e.get("denomination_text"),
                "year": e.get("year"), "candidates": " ".join(map(str, ids)),
                "queries": len(match.queries), "error": "",
            })

    out.close()

    print("\n===================== BILAN =====================")
    for model in models:
        recs = [r for r in records if r["model"] == model and not r.get("error")]
        if not recs:
            continue
        scored = [r for r in recs if r["expected"] is not None]
        hits = sum(1 for r in scored if r["rank"])
        times = [r["seconds"] for r in recs]
        tokens = [r["prompt_tokens"] for r in recs if r["prompt_tokens"]]
        print(f"{model:<16} top 5 : {hits}/{len(scored)}   temps moyen {statistics.mean(times):.1f} s "
              f"(max {max(times):.1f} s)   tokens entrée max {max(tokens) if tokens else '?'}")
    print(f"Détail : {out_file}")
    print(f"Requêtes Numista envoyées : {client.network_requests} "
          f"(total ce mois-ci : {client.used_this_month()}/{client.monthly_quota})")
    return 0


def main() -> int:
    s = get_settings()
    parser = argparse.ArgumentParser(description="POC d'identification Obole (Phase 0)")
    parser.add_argument("photos", nargs="*", help="photo de l'avers, puis éventuellement du revers")
    parser.add_argument("--model", default=s.vision_model, help=f"modèle Ollama (défaut {s.vision_model})")
    parser.add_argument("--num-ctx", type=int, default=s.vision_num_ctx, help="contexte du modèle (tokens)")
    parser.add_argument("--no-numista", action="store_true", help="extraction seule, sans Numista")
    parser.add_argument("--json", action="store_true", help="afficher l'extraction en JSON brut")
    parser.add_argument("--search", help="recherche manuelle dans Numista")
    parser.add_argument("--issuer", help="code émetteur Numista pour --search (ex. france)")
    parser.add_argument("--year", help="année grégorienne pour --search")
    parser.add_argument("--type", choices=["coin", "banknote", "token"], help="type d'objet pour --search")
    parser.add_argument("--count", type=int, default=10, help="nombre de résultats pour --search")
    parser.add_argument("--bench", action="store_true", help="banc d'essai sur --dir")
    parser.add_argument("--models", default=s.vision_model,
                        help="modèles à comparer (séparés par des virgules)")
    parser.add_argument("--dir", default=str(s.data_path / "test-photos"), help="dossier des photos de test")
    args = parser.parse_args()

    if args.search:
        return asyncio.run(cmd_search(args))
    if args.bench:
        return asyncio.run(cmd_bench(args))
    if not 1 <= len(args.photos) <= 2:
        parser.error("donner 1 ou 2 photos (avers, revers), ou --search, ou --bench")
    return asyncio.run(cmd_identify(args))


if __name__ == "__main__":
    sys.exit(main())
