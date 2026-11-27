"""Croisement de l'extraction IA avec le catalogue Numista.

1. Pays lu par le modèle -> code émetteur Numista (correspondance floue sur les noms en/fr).
2. Recherche /types économe : au plus MAX_SEARCHES requêtes, de la plus précise à la plus large.
"""

import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process, utils

from .numista import OBJECT_TYPE_COINS, NumistaClient, type_url

MAX_SEARCHES = 3
TOP_N = 5
ISSUER_MIN_SCORE = 90

# Noms fréquents chez les modèles qui ne ressemblent pas au nom Numista.
COUNTRY_ALIASES = {
    "usa": "United States",
    "united states of america": "United States",
    "us": "United States",
    "ussr": "Soviet Union",
    "uk": "United Kingdom",
    "great britain": "United Kingdom",
    "england": "United Kingdom",
    # Les billets en euro sont classés sous l'émetteur "Eurozone"
    "european union": "Eurozone",
    "eu": "Eurozone",
    "euro area": "Eurozone",
    "european central bank": "Eurozone",
}


class IssuerIndex:
    """Index nom -> code émetteur, construit à partir de /issuers en anglais et en français."""

    def __init__(self, issuers_en: list[dict], issuers_fr: list[dict]):
        self._names: dict[str, str] = {}
        for issuer in [*issuers_en, *issuers_fr]:
            name, code = issuer["name"], issuer["code"]
            # "Roman Empire (27 BC - 395 AD)" est aussi indexé comme "Roman Empire"
            short = re.sub(r"\s*\([^)]*\)\s*$", "", name)
            for key in {name, short}:
                current = self._names.get(key)
                # Même nom pour un regroupement ("france_section") et le pays ("france") :
                # garder le pays, plus précis pour la recherche
                if current is None or (current.endswith("_section") and not code.endswith("_section")):
                    self._names[key] = code

    def match(self, country: str | None) -> tuple[str | None, str | None, float]:
        """Renvoie (code, nom retenu, score 0-100) ou (None, None, 0) si rien d'assez proche."""
        if not country:
            return None, None, 0.0
        query = COUNTRY_ALIASES.get(country.strip().lower(), country)
        if query in self._names:
            return self._names[query], query, 100.0
        best = process.extractOne(
            query, self._names.keys(), scorer=fuzz.WRatio, processor=utils.default_process,
            score_cutoff=ISSUER_MIN_SCORE,
        )
        if best is None:
            return None, None, 0.0
        name, score, _ = best
        return self._names[name], name, float(score)


async def load_issuer_index(client: NumistaClient) -> IssuerIndex:
    """2 requêtes la première fois, puis cache local (90 jours)."""
    return IssuerIndex(await client.issuers("en"), await client.issuers("fr"))


async def object_type_ids(client: NumistaClient) -> dict[str, int]:
    """Identifiants Numista des grandes catégories (types racines : pièces, billets, exonumie)."""
    roots = {t["name"].lower(): t["id"] for t in await client.object_types() if not t.get("parent")}
    ids = {"coin": OBJECT_TYPE_COINS}
    for key, wanted in (("banknote", "banknote"), ("token", "exonumia")):
        found = next((i for name, i in roots.items() if wanted in name), None)
        if found is not None:
            ids[key] = found
    return ids


@dataclass
class Candidate:
    id: int
    title: str
    issuer: str | None
    min_year: int | None
    max_year: int | None
    obverse_thumbnail: str | None
    reverse_thumbnail: str | None

    @property
    def url(self) -> str:
        return type_url(self.id)

    @property
    def years(self) -> str:
        if self.min_year is None:
            return "?"
        return str(self.min_year) if self.min_year == self.max_year else f"{self.min_year}-{self.max_year}"


@dataclass
class MatchResult:
    issuer_code: str | None
    issuer_name: str | None
    issuer_score: float
    queries: list[dict] = field(default_factory=list)  # paramètres essayés, avec nb de résultats
    candidates: list[Candidate] = field(default_factory=list)


def build_queries(extraction: dict, issuer_code: str | None, object_type: int | None) -> list[dict]:
    """Requêtes de la plus précise à la plus large (dédupliquées, MAX_SEARCHES au plus)."""
    denomination = (extraction.get("denomination_text") or "").strip() or None
    keywords = [k for k in extraction.get("search_keywords") or [] if k and k.strip()]
    year = extraction.get("year")
    date = str(year) if isinstance(year, int) and extraction.get("year_visible") else None

    base = {"issuer": issuer_code, "object_type": object_type}
    queries = []
    if denomination:
        if date:
            queries.append({**base, "q": denomination, "date": date})
        queries.append({**base, "q": denomination})
    if keywords:
        # Les mots-clés reprennent souvent le pays : inutile de les mettre si l'émetteur est filtré
        country = (extraction.get("country") or "").lower()
        kept = [k for k in keywords if not issuer_code or k.lower() != country][:3]
        if kept:
            queries.append({**base, "q": " ".join(kept)})
    if issuer_code and denomination:
        # Dernier recours : sans filtre émetteur (le pays a pu être mal reconnu)
        queries.append({"object_type": object_type, "q": denomination, "date": date})

    unique = []
    for q in queries:
        if q not in unique:
            unique.append(q)
    return unique[:MAX_SEARCHES]


async def find_candidates(
    client: NumistaClient, index: IssuerIndex, extraction: dict, type_ids: dict[str, int]
) -> MatchResult:
    code, name, score = index.match(extraction.get("country"))
    result = MatchResult(issuer_code=code, issuer_name=name, issuer_score=score)
    object_type = type_ids.get(extraction.get("object_type") or "")
    for params in build_queries(extraction, code, object_type):
        data = await client.search_types(count=TOP_N, **params)
        result.queries.append({**params, "results": data.get("count", 0)})
        if data.get("types"):
            result.candidates = [
                Candidate(
                    id=t["id"],
                    title=t["title"],
                    issuer=(t.get("issuer") or {}).get("name"),
                    min_year=t.get("min_year"),
                    max_year=t.get("max_year"),
                    obverse_thumbnail=t.get("obverse_thumbnail"),
                    reverse_thumbnail=t.get("reverse_thumbnail"),
                )
                for t in data["types"][:TOP_N]
            ]
            break
    return result
