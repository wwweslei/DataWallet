"""Consultas agregadas e análise da carteira de títulos."""

from __future__ import annotations

from datetime import date

import pandas as pd

from database import Database
from models.titulo import Titulo
from services.indicadores import IndicadoresService
from services.tendencias import TendenciasService

class CarteiraService:
    """Calcula visão consolidada, evolução e classificação da carteira."""

    def __init__(self, database: Database) -> None:
        """Associa o serviço à persistência SQLite."""
        self.database = database

    def obter_resumo(self) -> dict[str, float]:
        """Retorna valores agregados da carteira."""
        titulos = self.database.listar_titulos()
        ativos_importados = self.database.listar_ativos()
        investido = sum(titulo.valor_investido for titulo in titulos) + sum(
            float(ativo["valor_investido"]) for ativo in ativos_importados
        )
        atual = sum(titulo.valor_atual for titulo in titulos) + sum(
            float(ativo["valor_investido"]) for ativo in ativos_importados
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
        for titulo in self.database.listar_titulos():
            atual = titulo.valor_atual
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
                    "Lucro (R$)": atual - titulo.valor_investido,
                    "Rentabilidade (%)": rentabilidade,
                    "Situação": "Cotação da carteira",
                }
            )
        for indice, ativo in enumerate(self.database.listar_ativos(), start=1):
            valor_investido = float(ativo["valor_investido"])
            linhas.append(
                {
                    "id": -indice,
                    "Título": str(ativo["nome"]),
                    "Tipo": str(ativo["categoria"]),
                    "Investido (R$)": valor_investido,
                    "Atual (R$)": valor_investido,
                    "Lucro (R$)": 0.0,
                    "Rentabilidade (%)": 0.0,
                    "Situação": "Cotação indisponível; usando valor investido",
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
                "Situação",
            ]
        )

    def obter_evolucao(self) -> pd.DataFrame:
        """Monta a evolução e inclui posições importadas no valor de hoje."""
        titulos = self.database.listar_titulos()
        ativos_importados = self.database.listar_ativos()
        if not titulos:
            valor_importado = sum(
                float(ativo["valor_investido"]) for ativo in ativos_importados
            )
            if ativos_importados:
                return pd.DataFrame(
                    {"data": [pd.Timestamp(date.today())], "valor": [valor_importado]}
                )
            return pd.DataFrame(columns=["data", "valor"])
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
        valores = pd.concat(linhas, axis=1).sum(axis=1)
        if ativos_importados:
            valores.loc[pd.Timestamp(date.today())] += sum(
                float(ativo["valor_investido"]) for ativo in ativos_importados
            )
        return pd.DataFrame({"data": indice, "valor": valores.to_numpy()})

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
        indicadores = IndicadoresService.indicadores_tecnicos(serie)
        performance = IndicadoresService.metricas_performance(serie)
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
