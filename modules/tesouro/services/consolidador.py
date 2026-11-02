"""Consolidação de posições com cálculo de preço médio ponderado."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from modules.tesouro.services.mapeador_colunas import normalizar_texto


@dataclass(frozen=True, slots=True)
class PosicaoImportada:
    """Posição normalizada extraída de uma linha de planilha."""

    ticker: str
    nome: str
    categoria: str
    quantidade: float
    preco_medio: float
    valor_investido: float
    vencimento: str | None = None


@dataclass(frozen=True, slots=True)
class PosicaoConsolidada(PosicaoImportada):
    """Posição agregada por ativo, sem linhas duplicadas."""


def normalizar_ticker(valor: str) -> str:
    """Normaliza a chave do ativo para deduplicar variações de caixa e espaços."""
    chave = re.sub(r"\s+", " ", valor.strip()).upper()
    if not chave:
        raise ValueError("O ativo não possui ticker ou nome identificador.")
    return chave


def consolidar_posicoes(
    posicoes: Iterable[PosicaoImportada],
) -> list[PosicaoConsolidada]:
    """Agrupa posições por ativo e calcula preço médio ponderado pelo custo."""
    grupos: dict[str, list[PosicaoImportada]] = defaultdict(list)
    for posicao in posicoes:
        ticker = normalizar_ticker(posicao.ticker)
        if (
            not math.isfinite(posicao.quantidade)
            or not math.isfinite(posicao.preco_medio)
            or not math.isfinite(posicao.valor_investido)
            or posicao.quantidade <= 0
            or posicao.preco_medio <= 0
            or posicao.valor_investido <= 0
        ):
            raise ValueError(f"Posição inválida para o ativo {ticker}.")
        grupos[ticker].append(posicao)

    consolidadas: list[PosicaoConsolidada] = []
    for ticker, linhas in sorted(grupos.items()):
        quantidade = sum(linha.quantidade for linha in linhas)
        valor_investido = sum(linha.valor_investido for linha in linhas)
        preco_medio = valor_investido / quantidade
        linha_detalhada = max(linhas, key=lambda linha: len(normalizar_texto(linha.nome)))
        categoria = next(
            (linha.categoria for linha in linhas if linha.categoria != "Outro"),
            linhas[0].categoria,
        )
        vencimento = next(
            (linha.vencimento for linha in linhas if linha.vencimento),
            None,
        )
        consolidadas.append(
            PosicaoConsolidada(
                ticker=ticker,
                nome=linha_detalhada.nome,
                categoria=categoria,
                quantidade=quantidade,
                preco_medio=preco_medio,
                valor_investido=valor_investido,
                vencimento=vencimento,
            )
        )
    return consolidadas
