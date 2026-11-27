"""Client de l'API Numista v3 avec cache local et compteur de quota.

Référence : swagger officiel v3.36.4 (https://en.numista.com/api/doc/index.php).
- Clé dans l'en-tête "Numista-API-Key".
- Plan gratuit : 2 000 requêtes / mois. L'endpoint /search_by_image est payant : jamais utilisé ici.

POC : le cache et le compteur sont des fichiers JSON dans DATA_DIR/cache/.
La Phase 1 les déplacera dans SQLite (tables numista_cache et api_usage).
"""

import asyncio
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

CACHE_TTL_SECONDS = 90 * 24 * 3600  # le catalogue change peu
QUOTA_SAFETY_MARGIN = 20  # requêtes gardées en réserve
OBJECT_TYPE_COINS = 147  # d'après la doc : "the ID 147 can be used to search for coins only"


class NumistaError(RuntimeError):
    pass


class QuotaError(NumistaError):
    pass


class NumistaClient:
    def __init__(self, api_key: str, base_url: str, cache_dir: Path, monthly_quota: int = 2000):
        if not api_key:
            raise NumistaError("NUMISTA_API_KEY absente du fichier .env")
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.cache_dir = cache_dir / "numista"
        self.usage_file = cache_dir / "numista_usage.json"
        self.monthly_quota = monthly_quota
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.network_requests = 0  # requêtes réellement envoyées pendant cette session

    # --- quota -----------------------------------------------------------------
    @staticmethod
    def _month() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def _read_usage(self) -> dict:
        try:
            return json.loads(self.usage_file.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def used_this_month(self) -> int:
        return self._read_usage().get(self._month(), 0)

    def _count_request(self) -> None:
        usage = self._read_usage()
        usage[self._month()] = usage.get(self._month(), 0) + 1
        self.usage_file.write_text(json.dumps(usage, indent=2), encoding="utf-8")
        self.network_requests += 1

    # --- cache -----------------------------------------------------------------
    def _cache_path(self, path: str, params: dict) -> Path:
        key = path + "?" + json.dumps(params, sort_keys=True, ensure_ascii=False)
        return self.cache_dir / (hashlib.sha256(key.encode("utf-8")).hexdigest() + ".json")

    # --- requêtes ----------------------------------------------------------------
    async def get(self, path: str, params: dict | None = None) -> dict:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        cache_file = self._cache_path(path, params)
        if cache_file.exists():
            cached = json.loads(cache_file.read_text(encoding="utf-8"))
            if time.time() - cached["fetched_at"] < CACHE_TTL_SECONDS:
                return cached["response"]

        if self.used_this_month() >= self.monthly_quota - QUOTA_SAFETY_MARGIN:
            raise QuotaError(
                f"Quota Numista presque atteint ({self.used_this_month()}/{self.monthly_quota} ce mois-ci)."
            )

        headers = {"Numista-API-Key": self._api_key}
        async with httpx.AsyncClient(timeout=30) as client:
            for attempt in range(2):
                self._count_request()
                response = await client.get(f"{self.base_url}{path}", params=params, headers=headers)
                if response.status_code != 429:
                    break
                # 429 = trop de requêtes simultanées OU quota mensuel atteint
                retry_after = response.headers.get("Retry-After", "")
                wait = int(retry_after) if retry_after.isdigit() else 5
                if attempt == 1 or wait > 60:
                    raise QuotaError(f"Numista : HTTP 429 ({_error_message(response)})")
                await asyncio.sleep(wait)

        if response.status_code == 401:
            raise NumistaError("Numista : clé API invalide ou absente (HTTP 401)")
        if response.status_code == 404:
            raise NumistaError(f"Numista : introuvable ({path})")
        if response.status_code != 200:
            raise NumistaError(f"Numista : HTTP {response.status_code} ({_error_message(response)})")

        data = response.json()
        cache_file.write_text(
            json.dumps({"path": path, "params": params, "fetched_at": time.time(), "response": data},
                       ensure_ascii=False),
            encoding="utf-8",
        )
        return data

    # --- endpoints utilisés --------------------------------------------------------
    async def issuers(self, lang: str = "en") -> list[dict]:
        return (await self.get("/issuers", {"lang": lang}))["issuers"]

    async def object_types(self) -> list[dict]:
        return (await self.get("/object_types"))["object_types"]

    async def search_types(
        self,
        q: str | None = None,
        *,
        issuer: str | None = None,
        object_type: int | None = None,
        date: str | None = None,
        lang: str = "en",
        count: int = 5,
    ) -> dict:
        """GET /types. `date` = année grégorienne (ou plage "1800-1850")."""
        params = {
            "q": q, "issuer": issuer, "object_type": object_type, "date": date,
            "lang": lang, "count": count, "order": "relevance" if q else None,
        }
        return await self.get("/types", params)

    async def get_type(self, type_id: int, lang: str = "en") -> dict:
        return await self.get(f"/types/{type_id}", {"lang": lang})


def _error_message(response: httpx.Response) -> str:
    try:
        return response.json().get("error_message", response.text[:200])
    except ValueError:
        return response.text[:200]


def type_url(type_id: int, lang: str = "fr") -> str:
    return f"https://{lang}.numista.com/{type_id}"
