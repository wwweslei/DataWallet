"""Classificação de tendência, sinais e scores de risco."""

from __future__ import annotations

import math

import pandas as pd


class TendenciasService:
    """Interpreta indicadores técnicos sem tratar sinais como recomendação."""

    @staticmethod
    def analisar(indicadores: pd.DataFrame, volatilidade: float = 0.0) -> dict[str, str | float]:
        """Gera classificação, sinal e scores normalizados entre zero e cem."""
        ultima = indicadores.iloc[-1] if not indicadores.empty else pd.Series(dtype=float)
        mm9, mm21 = ultima.get("mm9"), ultima.get("mm21")
        rsi, macd, macd_sinal = (
            ultima.get("rsi"),
            ultima.get("macd"),
            ultima.get("macd_sinal"),
        )
        if pd.notna(mm9) and pd.notna(mm21):
            if mm9 > mm21:
                tendencia = "Tendência de Alta"
            elif mm9 < mm21:
                tendencia = "Tendência de Baixa"
            else:
                tendencia = "Tendência Lateral"
        else:
            tendencia = "Dados insuficientes"
        if pd.notna(rsi) and rsi > 70:
            condicao_rsi = "Sobrecomprado"
        elif pd.notna(rsi) and rsi < 30:
            condicao_rsi = "Sobrevendido"
        else:
            condicao_rsi = "Neutro"

        sinal = "Sinal Neutro"
        if tendencia == "Tendência de Alta" and condicao_rsi != "Sobrecomprado":
            if pd.notna(macd) and pd.notna(macd_sinal) and macd >= macd_sinal:
                sinal = "Sinal de Compra"
        elif tendencia == "Tendência de Baixa" or condicao_rsi == "Sobrecomprado":
            sinal = "Sinal de Venda"

        fatores = [
            int(pd.notna(mm9) and pd.notna(mm21) and mm9 > mm21),
            int(pd.notna(rsi) and 30 <= rsi <= 70),
            int(pd.notna(macd) and pd.notna(macd_sinal) and macd > macd_sinal),
            int(pd.notna(ultima.get("roc")) and ultima.get("roc") > 0),
        ]
        score_tendencia = round(sum(fatores) / len(fatores) * 100)
        score_risco = round(min(100, max(0, 100 * (1 - math.exp(-max(volatilidade, 0) / 40)))))
        return {
            "tendencia": tendencia,
            "rsi_condicao": condicao_rsi,
            "sinal": sinal,
            "score_tendencia": float(score_tendencia),
            "score_risco": float(score_risco),
        }
