"""Configuration de l'application, lue depuis le fichier .env à la racine du dépôt."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> racine du dépôt = 2 niveaux au-dessus de backend/app
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    host: str = "127.0.0.1"
    port: int = 8000
    data_dir: Path = Path("./data")

    ollama_url: str = "http://localhost:11434"
    vision_model: str = "gemma4:e4b"
    # Taille du contexte du modèle (tokens). Deux images à 768 px en consomment ~1 800.
    # 8192 fait déborder qwen2.5vl:7b de la VRAM (8 Go) sur le CPU : beaucoup plus lent.
    vision_num_ctx: int = 4096
    # Phase 4 : recadrage automatique de l'objet (image envoyée au modèle et miniatures ;
    # les photos originales ne sont jamais modifiées)
    auto_crop: bool = True
    # Phase 4 : re-classement des candidats Numista par le modèle (photo + miniatures des fiches)
    rerank: bool = True

    numista_api_key: SecretStr = SecretStr("")
    numista_base_url: str = "https://api.numista.com/v3"
    numista_monthly_quota: int = 2000

    # Sauvegarde ZIP automatique (base + photos). 0 = désactivée.
    backup_interval_hours: float = 24
    backup_keep: int = 5  # nombre de sauvegardes automatiques conservées
    # Dossier des sauvegardes ; vide = DATA_DIR/backups. Idéalement un autre disque ou un dossier
    # synchronisé (OneDrive, clé USB...) : une copie sur le même disque ne protège pas d'une panne.
    backup_dir: Path | None = None

    # Frontend compilé (npm run build) servi par FastAPI ; ignoré s'il n'existe pas
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @property
    def data_path(self) -> Path:
        """DATA_DIR résolu : relatif à la racine du dépôt s'il n'est pas absolu."""
        return self.data_dir if self.data_dir.is_absolute() else (REPO_ROOT / self.data_dir).resolve()

    @property
    def backup_path(self) -> Path:
        if self.backup_dir is None or str(self.backup_dir) in ("", "."):
            return self.data_path / "backups"
        return self.backup_dir if self.backup_dir.is_absolute() else (REPO_ROOT / self.backup_dir).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
