"""Interface e adaptador HTTP configurável para provedores de cotações."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import requests

from database.repository import Database
from modules.tesouro.config import API_TIMEOUT_SECONDS, API_URL

logger = logging.getLogger(__name__)


class DataProviderError(RuntimeError):
    """Indica falha de configuração, comunicação ou formato de cotações."""


@dataclass(frozen=True, slots=True)
class PriceQuote:
    """Cotação normalizada recebida de uma fonte externa."""

    nome_titulo: str
    data: date
    preco: float


class TesouroDataProvider(Protocol):
    """Contrato para fontes oficiais ou adaptadores de cotações."""

    def buscar_cotacoes(self) -> list[PriceQuote]:
        """Obtém cotações normalizadas da fonte configurada."""
        ...


class JsonHttpTesouroProvider:
    """Busca cotações de uma API JSON com contrato documentado no README."""

    def __init__(self, url: str, timeout: float = API_TIMEOUT_SECONDS) -> None:
        """Cria um cliente HTTP para o endpoint de cotações."""
        if not url.strip():
            raise ValueError("A URL do provedor não pode estar vazia.")
        self.url = url
        self.timeout = timeout

    def buscar_cotacoes(self) -> list[PriceQuote]:
        """Lê uma lista JSON com nome_titulo, data e preco."""
        try:
            resposta = requests.get(
                self.url,
                timeout=self.timeout,
                headers={"Accept": "application/json"},
            )
            resposta.raise_for_status()
            conteudo = resposta.json()
        except (requests.RequestException, requests.exceptions.JSONDecodeError) as erro:
            logger.exception("Falha ao consultar provedor de cotações.")
            raise DataProviderError("Não foi possível consultar o provedor configurado.") from erro
        if not isinstance(conteudo, list):
            raise DataProviderError("A API deve retornar uma lista JSON de cotações.")
        cotacoes: list[PriceQuote] = []
        for item in conteudo:
            try:
                quote = PriceQuote(
                    nome_titulo=str(item["nome_titulo"]).strip(),
                    data=date.fromisoformat(str(item["data"])),
                    preco=float(item["preco"]),
                )
            except (KeyError, TypeError, ValueError) as erro:
                raise DataProviderError(
                    "Cotação inválida: esperado nome_titulo, data (AAAA-MM-DD) e preco."
                ) from erro
            if not quote.nome_titulo or not math.isfinite(quote.preco) or quote.preco <= 0:
                raise DataProviderError("Nome e preço da cotação devem ser válidos.")
            cotacoes.append(quote)
        if not cotacoes:
            raise DataProviderError("O provedor não retornou cotações.")
        return cotacoes


def atualizar_cotacoes(
    database: Database, provider: TesouroDataProvider
) -> int:
    """Registra cotações encontradas por nome e informa quantas foram atualizadas."""
    titulos = database.listar_titulos()
    ids_por_nome = {titulo.nome.casefold(): titulo.id for titulo in titulos}
    atualizadas = 0
    for cotacao in provider.buscar_cotacoes():
        titulo_id = ids_por_nome.get(cotacao.nome_titulo.casefold())
        if titulo_id is None:
            logger.info("Cotação ignorada para título não cadastrado: %s", cotacao.nome_titulo)
            continue
        database.registrar_preco(
            titulo_id, cotacao.data, cotacao.preco, fonte="api_configurada"
        )
        atualizadas += 1
    if atualizadas == 0:
        raise DataProviderError(
            "Nenhuma cotação retornada corresponde aos títulos cadastrados."
        )
    return atualizadas


def provedor_configurado() -> TesouroDataProvider | None:
    """Cria o adaptador HTTP somente quando TESOURO_API_URL estiver definida."""
    if not API_URL:
        return None
    return JsonHttpTesouroProvider(API_URL)
