"""Detecção de campos financeiros em planilhas de diferentes corretoras."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence


ALIASES: dict[str, tuple[str, ...]] = {
    "ticker": (
        "ticker",
        "codigo",
        "codigo do ativo",
        "papel",
        "ativo",
        "symbol",
        "simbolo",
    ),
    "nome": (
        "nome do ativo",
        "nome",
        "produto",
        "titulo",
        "descricao do ativo",
        "descricao",
        "ativo",
    ),
    "quantidade": (
        "quantidade atual",
        "quantidade",
        "posicao",
        "qtd",
        "saldo de ativos",
        "saldo",
    ),
    "preco_medio": (
        "preco medio",
        "pm",
        "preco de aquisicao",
        "preco unitario",
        "preco na aplicacao",
        "preco do titulo na aplicacao",
        "preco do titulo na aplicacao r",
        "preco medio unitario",
    ),
    "preco_atual": (
        "preco atual",
        "cotacao atual",
        "cotacao",
        "ultimo preco",
        "preco de mercado",
    ),
    "valor_investido": (
        "valor investido",
        "valor aplicado",
        "valor financeiro",
        "total investido",
        "valor de compra",
        "custo total",
        "valor aplicado total",
    ),
    "data_compra": (
        "data compra",
        "data da compra",
        "data operacao",
        "data da operacao",
        "data aplicacao",
        "data da aplicacao",
    ),
    "vencimento": ("vencimento", "data vencimento", "data de vencimento"),
    "categoria": (
        "categoria",
        "tipo",
        "tipo ativo",
        "tipo de ativo",
        "tipo do produto",
        "tipo de investimento",
        "classe",
        "classe de ativo",
        "segmento",
        "tipo produto",
        "tipo do ativo",
    ),
}


def normalizar_texto(valor: object) -> str:
    """Remove acentos e pontuação e normaliza espaços para comparar cabeçalhos."""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(caractere for caractere in texto if not unicodedata.combining(caractere))
    return re.sub(r"[^a-z0-9]+", " ", texto.casefold()).strip()


def mapear_colunas(colunas: Sequence[object]) -> dict[str, int]:
    """Associa nomes de coluna a campos canônicos usando aliases e similaridade."""
    normalizadas = [normalizar_texto(coluna) for coluna in colunas]
    mapeamento: dict[str, int] = {}
    for campo, aliases in ALIASES.items():
        aliases_normalizados = [normalizar_texto(alias) for alias in aliases]
        for alias in aliases_normalizados:
            indice = next(
                (
                    posicao
                    for posicao, nome in enumerate(normalizadas)
                    if nome == alias
                ),
                None,
            )
            if indice is not None:
                mapeamento[campo] = indice
                break
        if campo in mapeamento:
            continue
        candidatos = [
            (len(alias), indice)
            for indice, nome in enumerate(normalizadas)
            for alias in aliases_normalizados
            if len(alias) >= 4 and alias in nome
            and not (
                campo in {"ticker", "nome"}
                and (
                    nome.startswith(("tipo ", "categoria "))
                    or any(
                        termo in nome
                        for termo in (
                            "preco ",
                            "quantidade ",
                            "valor ",
                            "data ",
                            "rentabilidade ",
                        )
                    )
                    or (campo == "nome" and alias == "titulo")
                )
            )
        ]
        if candidatos:
            mapeamento[campo] = max(candidatos)[1]
    return mapeamento
