"""Indicadores técnicos e análise individual de títulos."""

import logging

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import DATABASE_PATH
from database import Database
from services.carteira import CarteiraService
from services.indicadores import IndicadoresService

logger = logging.getLogger(__name__)
database = Database(DATABASE_PATH)
service = CarteiraService(database)
titulos = database.listar_titulos()

st.title("Análise de tendências")
st.caption("Indicadores calculados somente sobre cotações salvas no histórico.")

if not titulos:
    st.info("Cadastre um título e registre cotações para começar a análise.")
else:
    titulo = st.selectbox(
        "Título analisado",
        titulos,
        format_func=lambda item: f"{item.nome} — {item.tipo}",
    )
    registros = database.obter_historico(titulo.id or 0)
    if len(registros) < 2:
        st.warning("Registre ao menos duas cotações em datas diferentes para gerar análise.")
    else:
        precos = pd.Series(
            [float(item["preco"]) for item in registros],
            index=pd.to_datetime([item["data"] for item in registros]),
            dtype="float64",
        )
        indicadores, tendencia, performance = service.obter_analise(titulo)
        with st.container(horizontal=True):
            st.metric("Tendência", str(tendencia["tendencia"]), border=True)
            st.metric("Sinal técnico", str(tendencia["sinal"]), border=True)
            st.metric("Score de tendência", f"{tendencia['score_tendencia']:.0f}/100", border=True)
            st.metric("Score de risco", f"{tendencia['score_risco']:.0f}/100", border=True)
        st.info(
            f"RSI: {tendencia['rsi_condicao']}. "
            "Scores e sinais são métricas descritivas, não recomendações de investimento."
        )

        figura = go.Figure()
        figura.add_trace(go.Scatter(x=indicadores.index, y=indicadores["preco"], name="Preço"))
        for periodo in (9, 21, 50, 200):
            figura.add_trace(
                go.Scatter(
                    x=indicadores.index,
                    y=indicadores[f"mm{periodo}"],
                    name=f"MM{periodo}",
                )
            )
        figura.add_trace(
            go.Scatter(
                x=indicadores.index,
                y=indicadores["bollinger_superior"],
                name="Bollinger superior",
                line={"dash": "dot"},
            )
        )
        figura.add_trace(
            go.Scatter(
                x=indicadores.index,
                y=indicadores["bollinger_inferior"],
                name="Bollinger inferior",
                line={"dash": "dot"},
                fill="tonexty",
                fillcolor="rgba(100, 149, 237, 0.08)",
            )
        )
        figura.update_layout(
            title="Preço, médias móveis e bandas de Bollinger",
            hovermode="x unified",
        )
        st.plotly_chart(
            figura,
            width="stretch",
            alt="Preço histórico e indicadores de tendência",
        )

        coluna_rsi, coluna_macd = st.columns(2)
        with coluna_rsi:
            figura_rsi = go.Figure()
            figura_rsi.add_trace(
                go.Scatter(
                    x=indicadores.index,
                    y=indicadores["rsi"],
                    name="RSI (14)",
                )
            )
            figura_rsi.add_hline(y=70, line_dash="dash", line_color="red")
            figura_rsi.add_hline(y=30, line_dash="dash", line_color="green")
            figura_rsi.update_layout(
                title="Índice de força relativa (RSI)",
                yaxis_range=[0, 100],
            )
            st.plotly_chart(
                figura_rsi,
                width="stretch",
                alt="Indicador RSI com limites de sobrecompra e sobrevenda",
            )
        with coluna_macd:
            figura_macd = go.Figure()
            figura_macd.add_trace(
                go.Scatter(
                    x=indicadores.index,
                    y=indicadores["macd"],
                    name="MACD",
                )
            )
            figura_macd.add_trace(
                go.Scatter(
                    x=indicadores.index,
                    y=indicadores["macd_sinal"],
                    name="Sinal",
                )
            )
            figura_macd.add_bar(
                x=indicadores.index,
                y=indicadores["macd_histograma"],
                name="Histograma",
            )
            figura_macd.update_layout(title="MACD")
            st.plotly_chart(
                figura_macd,
                width="stretch",
                alt="Indicador MACD, linha de sinal e histograma",
            )

        st.subheader("Desempenho histórico registrado")
        with st.container(horizontal=True):
            st.metric(
                "Rentabilidade acumulada",
                f"{performance['retorno_total']:.2f}%",
                border=True,
            )
            st.metric("Diária", f"{performance['retorno_diario']:.2f}%", border=True)
            st.metric("Semanal", f"{performance['retorno_semanal']:.2f}%", border=True)
            st.metric("Mensal", f"{performance['retorno_mensal']:.2f}%", border=True)
            st.metric("Anual", f"{performance['retorno_anual']:.2f}%", border=True)
            st.metric(
                "Volatilidade anualizada",
                f"{performance['volatilidade']:.2f}%",
                border=True,
            )
            st.metric(
                "Drawdown máximo",
                f"{performance['drawdown_maximo']:.2f}%",
                border=True,
            )

        with st.expander("Previsão linear simples"):
            quantidade = st.slider("Períodos futuros", min_value=1, max_value=30, value=5)
            try:
                previsao = IndicadoresService.previsao_linear(precos, quantidade)
                indices = pd.RangeIndex(len(indicadores), len(indicadores) + quantidade)
                tabela = pd.DataFrame(
                    {"período": indices, "preço estimado": previsao.to_numpy()}
                )
                st.dataframe(
                    tabela,
                    hide_index=True,
                    width="stretch",
                    alt="Projeção linear ilustrativa dos próximos preços",
                )
                st.caption(
                    "Extrapolação estatística simples; não é previsão garantida "
                    "nem recomendação."
                )
            except ValueError as erro:
                logger.warning("Previsão não calculada: %s", erro)
                st.warning(str(erro))

        st.subheader("Histórico diário registrado")
        st.dataframe(
            pd.DataFrame(registros),
            hide_index=True,
            width="stretch",
            alt="Cotações diárias registradas para o título",
        )
