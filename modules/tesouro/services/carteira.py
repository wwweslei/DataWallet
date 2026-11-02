"""Consultas agregadas e análise da carteira de títulos."""

from __future__ import annotations

import math
from datetime import date

import pandas as pd

from database.repository import Database
from modules.tesouro.models.titulo import Titulo
from modules.tesouro.services.indicadores import IndicadoresService
from modules.tesouro.services.mapeador_colunas import normalizar_texto
from modules.tesouro.services.tendencias import TendenciasService


class CarteiraService:
    """Calcula visão consolidada, evolução e classificação da carteira."""

    def __init__(self, database: Database) -> None:
        """Associa o serviço à persistência SQLite."""
        self.database = database

    def obter_resumo(self) -> dict[str, float]:
        """Retorna valores agregados da carteira."""
        titulos = self.database.listar_titulos()
        ativos_importados = self.database.listar_ativos()
        cotacoes = self._historico_cotacoes_ativos()
        investido = sum(titulo.valor_investido for titulo in titulos) + sum(
            float(ativo["valor_investido"]) for ativo in ativos_importados
        )
        atual = sum(titulo.valor_atual for titulo in titulos) + sum(
            self._valor_atual_ativo(ativo, cotacoes)
            for ativo in ativos_importados
        )
        return {
            "valor_investido": investido,
            "valor_atual": atual,
            "lucro": atual - investido,
            "rentabilidade": (atual / investido - 1) * 100 if investido else 0.0,
        }

    def obter_ranking(self) -> pd.DataFrame:
        """Ordena títulos e ativos importados pela rentabilidade registrada."""
        linhas: list[dict[str, float | str | int]] = []
        cotacoes = self._historico_cotacoes_ativos()
        for titulo in self.database.listar_titulos():
            atual = titulo.valor_atual
            lucro = atual - titulo.valor_investido
            juros = self._estimar_juros_semestrais(titulo.nome, atual)
            rentabilidade = (
                (atual / titulo.valor_investido - 1) * 100
                if titulo.valor_investido
                else 0.0
            )
            linhas.append(
                {
                    "id": titulo.id or 0,
                    "Título": titulo.nome,
                    "Tipo": titulo.tipo,
                    "Investido (R$)": titulo.valor_investido,
                    "Atual (R$)": atual,
                    "Lucro (R$)": lucro,
                    "Rentabilidade (%)": rentabilidade,
                    "Juros semestrais estimados (R$)": juros,
                    "Rendimentos (R$)": lucro,
                    "Juros + rendimentos (R$)": juros + lucro,
                    "Situação": "Cotação da carteira",
                }
            )
        for indice, ativo in enumerate(self.database.listar_ativos(), start=1):
            valor_investido = float(ativo["valor_investido"])
            atual = self._valor_atual_ativo(ativo, cotacoes)
            lucro = atual - valor_investido
            juros = self._estimar_juros_semestrais(str(ativo["nome"]), atual)
            tem_cotacao = normalizar_texto(ativo["ticker"]) in cotacoes
            linhas.append(
                {
                    "id": -indice,
                    "Título": str(ativo["nome"]),
                    "Tipo": str(ativo["categoria"]),
                    "Investido (R$)": valor_investido,
                    "Atual (R$)": atual,
                    "Lucro (R$)": lucro,
                    "Rentabilidade (%)": (
                        (atual / valor_investido - 1) * 100
                        if valor_investido
                        else 0.0
                    ),
                    "Juros semestrais estimados (R$)": juros,
                    "Rendimentos (R$)": lucro,
                    "Juros + rendimentos (R$)": juros + lucro,
                    "Situação": (
                        "Cotação do Tesouro disponível"
                        if tem_cotacao
                        else "Cotação indisponível; usando valor investido"
                    ),
                }
            )
        return pd.DataFrame(linhas).sort_values(
            "Rentabilidade (%)", ascending=False, ignore_index=True
        ) if linhas else pd.DataFrame(
            columns=[
                "id",
                "Título",
                "Tipo",
                "Investido (R$)",
                "Atual (R$)",
                "Lucro (R$)",
                "Rentabilidade (%)",
                "Juros semestrais estimados (R$)",
                "Rendimentos (R$)",
                "Juros + rendimentos (R$)",
                "Situação",
            ]
        )

    @staticmethod
    def _estimar_juros_semestrais(nome: str, valor_atual: float) -> float:
        """Estima um cupom usando a taxa padrão e o valor atual como referência."""
        nome_normalizado = normalizar_texto(nome)
        if "juros semestrais" not in nome_normalizado:
            return 0.0
        if "ipca" in nome_normalizado:
            taxa_anual = 0.06
        elif "prefixado" in nome_normalizado:
            taxa_anual = 0.10
        else:
            return 0.0
        return valor_atual * (math.sqrt(1 + taxa_anual) - 1)

    def obter_evolucao(self) -> pd.DataFrame:
        """Monta a evolução patrimonial usando snapshots de preço disponíveis."""
        titulos = self.database.listar_titulos()
        ativos_importados = self.database.listar_ativos()
        if not titulos and not ativos_importados:
            return pd.DataFrame(columns=["data", "valor"])
        cotacoes_ativos = self._historico_cotacoes_ativos()
        datas: set[pd.Timestamp] = {pd.Timestamp(date.today())}
        historicos: dict[int, pd.Series] = {}
        for titulo in titulos:
            registros = self.database.obter_historico(titulo.id or 0)
            if registros:
                serie = pd.Series(
                    {pd.Timestamp(item["data"]): float(item["preco"]) for item in registros}
                ).sort_index()
                serie = serie.loc[serie.index >= pd.Timestamp(titulo.data_compra)]
                historicos[titulo.id or 0] = serie
                datas.update(serie.index)
            datas.add(pd.Timestamp(titulo.data_compra))
            datas.add(pd.Timestamp(date.today()))
        historicos_ativos: list[tuple[pd.Timestamp, pd.Series]] = []
        for ativo in ativos_importados:
            data_base = pd.Timestamp(ativo["data_atualizacao"]).normalize()
            valor_investido = float(ativo["valor_investido"])
            chave = normalizar_texto(ativo["ticker"])
            serie = cotacoes_ativos.get(
                chave,
                pd.Series(dtype="float64", index=pd.DatetimeIndex([])),
            )
            serie = serie.loc[serie.index >= data_base] * float(ativo["quantidade"])
            serie = pd.concat(
                [
                    pd.Series([valor_investido], index=[data_base], dtype="float64"),
                    serie,
                    pd.Series(
                        [self._valor_atual_ativo(ativo, cotacoes_ativos)],
                        index=[pd.Timestamp(date.today())],
                        dtype="float64",
                    ),
                ]
            ).groupby(level=0).last().sort_index()
            historicos_ativos.append((data_base, serie))
            datas.update(serie.index)
        indice = pd.DatetimeIndex(sorted(datas))
        linhas: list[pd.Series] = []
        for titulo in titulos:
            serie = historicos.get(titulo.id or 0, pd.Series(dtype="float64"))
            serie = pd.concat(
                [
                    pd.Series(
                        [titulo.preco_compra],
                        index=[pd.Timestamp(titulo.data_compra)],
                        dtype="float64",
                    ),
                    serie,
                    pd.Series(
                        [titulo.preco_atual or titulo.preco_compra],
                        index=[pd.Timestamp(date.today())],
                        dtype="float64",
                    ),
                ]
            ).groupby(level=0).last().sort_index()
            posicao = serie.reindex(indice).ffill()
            posicao = posicao.fillna(titulo.preco_compra)
            posicao.loc[indice < pd.Timestamp(titulo.data_compra)] = 0.0
            linhas.append(posicao * titulo.quantidade)
        for data_base, serie in historicos_ativos:
            posicao = serie.reindex(indice).ffill().fillna(0.0)
            posicao.loc[indice < data_base] = 0.0
            linhas.append(posicao)
        valores = pd.concat(linhas, axis=1).sum(axis=1)
        return pd.DataFrame({"data": indice, "valor": valores.to_numpy()})

    def _historico_cotacoes_ativos(self) -> dict[str, pd.Series]:
        """Agrupa preços cotados por nome normalizado para ativos importados."""
        precos: dict[str, dict[pd.Timestamp, float]] = {}
        for cotacao in self.database.listar_historico_cotacoes_tesouro():
            preco = cotacao["preco_venda"]
            if preco is None:
                preco = cotacao["preco_compra"]
            if preco is None:
                continue
            chave = normalizar_texto(cotacao["titulo"])
            data = pd.Timestamp(cotacao["data_coleta"]).normalize()
            precos.setdefault(chave, {})[data] = float(preco)
        return {
            chave: pd.Series(valores, dtype="float64").sort_index()
            for chave, valores in precos.items()
        }

    @staticmethod
    def _valor_atual_ativo(
        ativo: dict[str, object], cotacoes: dict[str, pd.Series]
    ) -> float:
        """Avalia um ativo pela cotação mais recente ou pelo custo importado."""
        historico = cotacoes.get(normalizar_texto(ativo["ticker"]))
        if historico is None or historico.empty:
            return float(ativo["valor_investido"])
        return float(ativo["quantidade"]) * float(historico.iloc[-1])

    def obter_analise(
        self, titulo: Titulo
    ) -> tuple[pd.DataFrame, dict[str, str | float], dict[str, float]]:
        """Retorna indicadores, interpretação da tendência e performance."""
        registros = self.database.obter_historico(titulo.id or 0)
        serie = pd.Series(
            [float(item["preco"]) for item in registros],
            index=pd.to_datetime([item["data"] for item in registros]),
            dtype="float64",
        )
        return self.analisar_precos(serie)

    def obter_historico_ativo(
        self, ativo: dict[str, object]
    ) -> list[dict[str, str | float]]:
        """Retorna cotações do Tesouro para uma posição importada."""
        chaves = {
            normalizar_texto(ativo["ticker"]),
            normalizar_texto(ativo["nome"]),
        }
        data_base = pd.Timestamp(str(ativo["data_atualizacao"])).normalize()
        registros_por_data: dict[pd.Timestamp, float] = {}
        for cotacao in self.database.listar_historico_cotacoes_tesouro():
            if normalizar_texto(cotacao["titulo"]) not in chaves:
                continue
            data = pd.Timestamp(str(cotacao["data_coleta"])).normalize()
            preco = cotacao["preco_venda"] or cotacao["preco_compra"]
            if data >= data_base and preco is not None:
                registros_por_data[data] = float(preco)
        return [
            {
                "data": data.date().isoformat(),
                "preco": preco,
                "fonte": "Tesouro Direto",
            }
            for data, preco in sorted(registros_por_data.items())
        ]

    @staticmethod
    def analisar_precos(
        precos: pd.Series,
    ) -> tuple[pd.DataFrame, dict[str, str | float], dict[str, float]]:
        """Calcula os indicadores técnicos e métricas para uma série de preços."""
        indicadores = IndicadoresService.indicadores_tecnicos(precos)
        performance = IndicadoresService.metricas_performance(precos)
        tendencia = TendenciasService.analisar(
            indicadores, volatilidade=performance["volatilidade"]
        )
        return indicadores, tendencia, performance

    def ranking_com_tendencias(self) -> pd.DataFrame:
        """Enriquece o ranking com scores e risco técnico."""
        ranking = self.obter_ranking()
        if ranking.empty:
            return ranking
        tendencias: dict[int, dict[str, str | float]] = {}
        for titulo in self.database.listar_titulos():
            _, tendencia, _ = self.obter_analise(titulo)
            tendencias[titulo.id or 0] = {
                    "Score de tendência": tendencia["score_tendencia"],
                    "Score de risco": tendencia["score_risco"],
                    "Sinal": tendencia["sinal"],
                }
        for coluna in ("Score de tendência", "Score de risco", "Sinal"):
            ranking[coluna] = ranking["id"].map(
                {titulo_id: valores[coluna] for titulo_id, valores in tendencias.items()}
            )
        return ranking
