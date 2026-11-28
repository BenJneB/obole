"""Client de l'API Numista v3 avec cache SQLite et compteur de quota.

Référence : swagger officiel v3.36.4 (https://en.numista.com/api/doc/index.php).
- Clé dans l'en-tête "Numista-API-Key".
- Plan gratuit : 2 000 requêtes / mois. L'endpoint /search_by_image est payant : jamais utilisé ici.
- Toute réponse du catalogue est mise en cache (table numista_cache, 90 jours) ; chaque requête
  réellement envoyée est comptée (table api_usage, par mois).
- Ma collection (Phase 4) : jeton OAuth « client_credentials » obtenu avec la clé API (scopes
  view_collection + edit_collection), puis /users/{user_id}/collected_items. Ces endpoints ne sont
  pas dans le swagger consulté en Phase 0 : formats déduits du forum Numista, lus prudemment.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import Engine
from sqlmodel import Session

from ..db import cache_key
from ..models import ApiUsage, NumistaCache, utcnow

CACHE_TTL = timedelta(days=90)  # le catalogue change peu
QUOTA_SAFETY_MARGIN = 20  # requêtes gardées en réserve
OBJECT_TYPE_COINS = 147  # d'après la doc : "the ID 147 can be used to search for coins only"
# Les deux sont nécessaires, même pour écrire (forum Numista : edit_collection seul -> refusé)
COLLECTION_SCOPES = "view_collection,edit_collection"


class NumistaError(RuntimeError):
    pass


class QuotaError(NumistaError):
    pass


class NumistaNotFound(NumistaError):
    pass


class NumistaAuthError(NumistaError):
    """Accès OAuth à la collection refusé (jeton expiré, droits insuffisants)."""


class NumistaClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        engine: Engine,
        monthly_quota: int = 2000,
        transport: httpx.AsyncBaseTransport | None = None,  # injecté par les tests
    ):
        self._api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.engine = engine
        self.monthly_quota = monthly_quota
        self._transport = transport
        self.network_requests = 0  # requêtes réellement envoyées par ce client
        self._oauth: tuple[str, int, datetime] | None = None  # (jeton, user_id, expiration)

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    # --- quota -----------------------------------------------------------------
    @staticmethod
    def _month() -> str:
        return datetime.now(UTC).strftime("%Y-%m")

    def used_this_month(self) -> int:
        with Session(self.engine) as session:
            usage = session.get(ApiUsage, self._month())
            return usage.numista_requests if usage else 0

    def remaining(self) -> int:
        return max(0, self.monthly_quota - QUOTA_SAFETY_MARGIN - self.used_this_month())

    def _count_request(self) -> None:
        with self.engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO api_usage (month, numista_requests) VALUES (?, 1) "
                "ON CONFLICT(month) DO UPDATE SET numista_requests = numista_requests + 1",
                (self._month(),),
            )
        self.network_requests += 1

    # --- cache -----------------------------------------------------------------
    def _cache_get(self, key: str) -> dict | None:
        with Session(self.engine) as session:
            row = session.get(NumistaCache, key)
            if row and utcnow() - row.fetched_at < CACHE_TTL:
                return json.loads(row.response_json)
        return None

    def _cache_put(self, key: str, data: dict) -> None:
        with Session(self.engine) as session:
            session.merge(NumistaCache(key=key, response_json=json.dumps(data, ensure_ascii=False)))
            session.commit()

    # --- requêtes ----------------------------------------------------------------
    async def _send(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        json_body: dict | None = None,
        bearer: str | None = None,
    ) -> httpx.Response:
        """Envoie une requête (comptée dans le quota) ; gère 429, erreurs réseau et codes d'erreur.
        Renvoie la réponse en cas de succès (2xx)."""
        if not self.configured:
            raise NumistaError("NUMISTA_API_KEY absente du fichier .env")
        if self.remaining() <= 0:
            raise QuotaError(
                f"Quota Numista presque atteint ({self.used_this_month()}/{self.monthly_quota} ce mois-ci)."
            )
        headers = {"Numista-API-Key": self._api_key}
        if bearer:
            headers["Authorization"] = f"Bearer {bearer}"
        try:
            async with httpx.AsyncClient(timeout=30, transport=self._transport) as client:
                for attempt in range(2):
                    self._count_request()
                    response = await client.request(
                        method, f"{self.base_url}{path}", params=params, json=json_body, headers=headers
                    )
                    if response.status_code != 429:
                        break
                    # 429 = trop de requêtes simultanées OU quota mensuel atteint
                    retry_after = response.headers.get("Retry-After", "")
                    wait = int(retry_after) if retry_after.isdigit() else 5
                    if attempt == 1 or wait > 60:
                        raise QuotaError(f"Numista : HTTP 429 ({_error_message(response)})")
                    await asyncio.sleep(wait)
        except httpx.HTTPError as exc:
            raise NumistaError(f"Numista injoignable : {exc}") from exc

        if response.status_code == 401:
            if bearer:
                raise NumistaAuthError(f"Numista : accès à la collection refusé ({_error_message(response)})")
            raise NumistaError("Numista : clé API invalide ou absente (HTTP 401)")
        if response.status_code == 403:
            raise NumistaAuthError(f"Numista : accès refusé (HTTP 403, {_error_message(response)})")
        if response.status_code == 404:
            raise NumistaNotFound(f"Numista : introuvable ({path})")
        if not 200 <= response.status_code < 300:
            raise NumistaError(f"Numista : HTTP {response.status_code} ({_error_message(response)})")
        return response

    @staticmethod
    def _json(response: httpx.Response) -> dict:
        try:
            data = response.json()
        except ValueError as exc:
            raise NumistaError(f"Numista : réponse illisible ({response.text[:200]})") from exc
        if not isinstance(data, dict):
            raise NumistaError(f"Numista : réponse inattendue ({response.text[:200]})")
        return data

    async def get(self, path: str, params: dict | None = None) -> dict:
        """GET du catalogue, avec cache (90 jours)."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        key = cache_key(path, params)
        cached = self._cache_get(key)
        if cached is not None:
            return cached
        data = self._json(await self._send("GET", path, params=params))
        self._cache_put(key, data)
        return data

    # --- ma collection (OAuth, jamais en cache) -------------------------------------
    async def _token(self, force: bool = False) -> tuple[str, int]:
        """Jeton OAuth « client_credentials » : donne accès à la collection du propriétaire de la
        clé API. Gardé en mémoire jusqu'à son expiration (2 h d'après Numista)."""
        now = datetime.now(UTC)
        if not force and self._oauth and self._oauth[2] > now:
            return self._oauth[0], self._oauth[1]
        data = self._json(await self._send("GET", "/oauth_token", params={
            "grant_type": "client_credentials", "scope": COLLECTION_SCOPES,
        }))
        token, user_id = data.get("access_token"), data.get("user_id")
        if not token or not isinstance(user_id, int):
            raise NumistaAuthError(f"Numista : jeton OAuth inattendu (champs reçus : {sorted(data)})")
        expires_in = data.get("expires_in") if isinstance(data.get("expires_in"), int) else 3600
        self._oauth = (token, user_id, now + timedelta(seconds=max(60, expires_in - 60)))
        return token, user_id

    async def _collection_request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """`path` contient « {user_id} ». Un jeton expiré est renouvelé une fois."""
        token, user_id = await self._token()
        try:
            return await self._send(method, path.format(user_id=user_id), bearer=token, **kwargs)
        except NumistaAuthError:
            token, user_id = await self._token(force=True)
            return await self._send(method, path.format(user_id=user_id), bearer=token, **kwargs)

    async def collection_user_id(self) -> int:
        return (await self._token())[1]

    async def collected_items(self, lang: str = "fr") -> list[dict]:
        """Objets de ma collection Numista (GET /users/{id}/collected_items)."""
        response = await self._collection_request(
            "GET", "/users/{user_id}/collected_items", params={"lang": lang}
        )
        data = self._json(response)
        items = data.get("items", data.get("collected_items", []))
        if not isinstance(items, list):
            raise NumistaError(f"Numista : liste d'objets inattendue (champs reçus : {sorted(data)})")
        return items

    async def add_collected_item(self, payload: dict) -> dict:
        """Ajoute un objet à ma collection Numista (POST /users/{id}/collected_items)."""
        response = await self._collection_request(
            "POST", "/users/{user_id}/collected_items", json_body=payload
        )
        return self._json(response) if response.content else {}

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
