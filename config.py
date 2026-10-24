"""Configurações centrais do Tesouro Tracker."""

from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
DATABASE_DIR = BASE_DIR / "database"
DATABASE_PATH = Path(
    os.getenv("TESOURO_DATABASE_PATH", str(DATABASE_DIR / "tesouro.db"))
)
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
API_URL = os.getenv("TESOURO_API_URL", "").strip()
API_TIMEOUT_SECONDS = float(os.getenv("TESOURO_API_TIMEOUT_SECONDS", "15"))
TIPOS_TITULO = (
    "Tesouro Selic",
    "Tesouro Prefixado",
    "Tesouro IPCA+",
    "Tesouro RendA+",
    "Tesouro Educa+",
    "Outro",
)
