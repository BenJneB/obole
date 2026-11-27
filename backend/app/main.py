"""Point d'entrée du serveur Obole.

Lancer (depuis backend/) : uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
Documentation interactive de l'API : http://127.0.0.1:8000/docs
"""

import logging

from .factory import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s : %(message)s")

logging.getLogger("httpx").setLevel(logging.WARNING)  # une ligne par requête : trop bavard

app = create_app()
