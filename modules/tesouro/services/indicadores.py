"""Cálculo de métricas financeiras e indicadores técnicos."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np
import pandas as pd

class IndicadoresService:
    """Calcula métricas de desempenho e indicadores de séries de preços."""

    @staticmethod
    def indicadores_tecnicos(precos: Sequence[float] | pd.Series) -> pd.DataFrame:
        """Calcula médias móveis, RSI, MACD, Bollinger e ROC."""
        serie = pd.Series(precos, dtype="float64").dropna()
        if not np.isfinite(serie).all() or (serie <= 0).any():
            raise ValueError("Os preços devem ser números finitos positivos.")
        resultado = pd.DataFrame({"preco": serie})
        for periodo in (9, 21, 50, 200):
            resultado[f"mm{periodo}"] = serie.rolling(
                window=periodo, min_periods=periodo
            ).mean()

        variacao = serie.diff()
        ganhos = variacao.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        perdas = -variacao.clip(upper=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
        razao = ganhos.div(perdas.replace(0, np.nan))
        resultado["rsi"] = 100 - (100 / (1 + razao))
        resultado.loc[(perdas == 0) & (ganhos > 0), "rsi"] = 100.0
        resultado.loc[(ganhos == 0) & (perdas > 0), "rsi"] = 0.0

        media_12 = serie.ewm(span=12, adjust=False, min_periods=12).mean()
        media_26 = serie.ewm(span=26, adjust=False, min_periods=26).mean()
        resultado["macd"] = media_12 - media_26
        resultado["macd_sinal"] = resultado["macd"].ewm(
            span=9, adjust=False, min_periods=9
        ).mean()
        resultado["macd_histograma"] = resultado["macd"] - resultado["macd_sinal"]

        media_20 = serie.rolling(window=20, min_periods=20).mean()
        desvio_20 = serie.rolling(window=20, min_periods=20).std(ddof=0)
        resultado["bollinger_centro"] = media_20
        resultado["bollinger_superior"] = media_20 + 2 * desvio_20
        resultado["bollinger_inferior"] = media_20 - 2 * desvio_20
        resultado["roc"] = serie.pct_change(periods=12) * 100
        return resultado

    @staticmethod
    def metricas_performance(
        precos: Sequence[float] | pd.Series,
    ) -> dict[str, float]:
        """Calcula retornos, evolução, drawdown e volatilidade anualizada."""
        serie = pd.Series(precos, dtype="float64").dropna()
        if serie.empty:
            return {
                "retorno_total": 0.0,
                "retorno_diario": 0.0,
                "retorno_semanal": 0.0,
                "retorno_mensal": 0.0,
                "retorno_anual": 0.0,
                "drawdown_maximo": 0.0,
                "volatilidade": 0.0,
                "evolucao": 0.0,
            }
        if not np.isfinite(serie).all() or (serie <= 0).any():
            raise ValueError("Os preços devem ser números finitos positivos.")
        primeiro, ultimo = float(serie.iloc[0]), float(serie.iloc[-1])
        retornos = serie.pct_change().dropna()
        drawdowns = serie.div(serie.cummax()).sub(1)

        def retorno_periodo(dias: int) -> float:
            """Retorna a variação em relação ao preço de N observações atrás."""
            base = float(serie.iloc[max(0, len(serie) - 1 - dias)])
            return (ultimo / base - 1) * 100

        return {
            "retorno_total": (ultimo / primeiro - 1) * 100,
            "retorno_diario": retorno_periodo(1),
            "retorno_semanal": retorno_periodo(5),
            "retorno_mensal": retorno_periodo(21),
            "retorno_anual": retorno_periodo(252),
            "drawdown_maximo": float(drawdowns.min() * 100),
            "volatilidade": float(retornos.std(ddof=1) * np.sqrt(252) * 100)
            if len(retornos) > 1
            else 0.0,
            "evolucao": ultimo,
        }

    @staticmethod
    def previsao_linear(
        precos: Sequence[float] | pd.Series, periodos: int = 5
    ) -> pd.Series:
        """Projeta os próximos preços com uma regressão linear simples."""
        serie = pd.Series(precos, dtype="float64").dropna()
        if periodos < 1:
            raise ValueError("O número de períodos deve ser positivo.")
        if len(serie) < 2:
            raise ValueError("São necessários ao menos dois preços para a previsão.")
        x = np.arange(len(serie), dtype="float64")
        slope, intercept = np.polyfit(x, serie.to_numpy(), 1)
        x_futuro = np.arange(len(serie), len(serie) + periodos, dtype="float64")
        return pd.Series(intercept + slope * x_futuro, name="previsao")
