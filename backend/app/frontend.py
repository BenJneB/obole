"""Service des fichiers du frontend compilé (frontend/dist) : un seul processus en production.

- /assets/* : noms contenant un hash, contenu figé -> cache d'un an.
- index.html, sw.js, manifest : toujours revalidés, pour que les mises à jour arrivent tout de suite.
- Toute autre adresse hors /api renvoie index.html (routes gérées par React).
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

NO_CACHE = {"Cache-Control": "no-cache"}
IMMUTABLE = {"Cache-Control": "public, max-age=31536000, immutable"}


def mount_frontend(app: FastAPI, dist: Path) -> bool:
    index = dist / "index.html"
    if not index.is_file():
        return False
    root = dist.resolve()

    @app.api_route("/{path:path}", methods=["GET", "HEAD"], include_in_schema=False)
    def frontend(path: str) -> FileResponse:
        if path == "api" or path.startswith("api/"):
            raise HTTPException(404, "Route d'API inconnue")
        if path:
            file = (root / path).resolve()
            if file.is_relative_to(root) and file.is_file():
                headers = IMMUTABLE if path.startswith("assets/") else NO_CACHE
                return FileResponse(file, headers=headers)
            if "." in path.rsplit("/", 1)[-1]:
                raise HTTPException(404, "Fichier introuvable")  # ex. image manquante : pas index.html
        return FileResponse(index, headers=NO_CACHE)

    return True
