"""Configurações compartilhadas da aplicação."""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

DATABASE_DIR = BASE_DIR / "database"
DATABASE_PATH = Path(
    os.getenv(
        "DATAWALLET_DATABASE_PATH",
        os.getenv("TESOURO_DATABASE_PATH", str(DATABASE_DIR / "tesouro.db")),
    )
)
SCHEMA_PATH = DATABASE_DIR / "schema.sql"
