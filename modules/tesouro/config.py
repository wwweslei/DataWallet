"""Configurações específicas do módulo Tesouro."""

import os

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
