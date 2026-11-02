"""Composição da navegação dos módulos de investimento."""

from modules.acoes.navigation import get_pages as get_acoes_pages
from modules.fii.navigation import get_pages as get_fii_pages
from modules.tesouro.navigation import get_pages as get_tesouro_pages


def get_pages():
    """Retorna as páginas fornecidas pelos módulos disponíveis."""
    return [
        *get_tesouro_pages(),
        *get_fii_pages(),
        *get_acoes_pages(),
    ]
