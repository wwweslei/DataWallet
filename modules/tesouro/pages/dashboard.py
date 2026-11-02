"""Dashboard consolidado de desempenho da carteira."""

import pandas as pd
import plotly.express as px
import streamlit as st

from config import DATABASE_PATH
from database.repository import Database
from modules.tesouro.services.carteira import CarteiraService
from modules.tesouro.services.tesouro_scraper import TesouroDiretoScraper
from modules.tesouro.utils.helpers import (
    exportar_csv,
    exportar_excel,
    exportar_pdf,
    formatar_moeda,
)

database = Database(DATABASE_PATH)
service = CarteiraService(database)
scraper = TesouroDiretoScraper(database)

st.title("Acompanhamento da carteira")
st.caption("Visão consolidada dos títulos e das cotações registradas.")

col_refresh, col_info = st.columns([1, 3], vertical_alignment="bottom")
with col_refresh:
    if st.button(
        "Atualizar Cotações",
        icon=":material/refresh:",
        type="primary",
    ):
        resultado_atualizacao = scraper.atualizar_cotacoes()
        st.session_state["tesouro_ultima_atualizacao"] = resultado_atualizacao
        st.rerun()
with col_info:
    st.caption(
        "Cotações públicas do Tesouro Direto. Se a fonte estiver indisponível "
        "ou mudar de formato, o último snapshot salvo será usado."
    )

resultado_atualizacao = st.session_state.get("tesouro_ultima_atualizacao")
if resultado_atualizacao is not None:
    if resultado_atualizacao.usou_fallback:
        st.warning(
            "Fonte de cotações indisponível; exibindo os últimos snapshots salvos. "
            f"{resultado_atualizacao.erro or ''}",
            title="Usando cache local",
        )
    else:
        st.success(
            f"Coleta concluída: {len(resultado_atualizacao.cotacoes)} título(s) "
            "registrados no histórico."
        )

resumo = service.obter_resumo()
ativos_importados = database.listar_ativos()
with st.container(horizontal=True):
    st.metric("Total investido", formatar_moeda(resumo["valor_investido"]), border=True)
    st.metric("Valor atual", formatar_moeda(resumo["valor_atual"]), border=True)
    st.metric(
        "Lucro / prejuízo",
        formatar_moeda(resumo["lucro"]),
        f"{resumo['rentabilidade']:.2f}%",
        border=True,
    )
    st.metric("Posições", len(database.listar_titulos()) + len(ativos_importados), border=True)
if ativos_importados:
    st.caption(
        "Ativos importados sem cotação de mercado são avaliados pelo valor "
        "investido registrado no arquivo."
    )

ranking = service.obter_ranking()
cotacoes = scraper.obter_ultimas_cotacoes()
if ranking.empty:
    st.info("Sua carteira está vazia. Cadastre o primeiro título na página Carteira.")
else:
    evolucao = service.obter_evolucao()
    esquerda, direita = st.columns(2)
    with esquerda:
        with st.container(border=True):
            st.subheader("Evolução patrimonial")
            figura = px.line(
                evolucao,
                x="data",
                y="valor",
                labels={"data": "Data", "valor": "Valor (R$)"},
                markers=True,
            )
            figura.update_layout(hovermode="x unified")
            st.plotly_chart(
                figura,
                width="stretch",
                alt="Evolução do valor patrimonial da carteira",
            )
    with direita:
        with st.container(border=True):
            st.subheader("Distribuição atual")
            distribuicao = ranking[ranking["Atual (R$)"] > 0]
            figura = px.pie(
                distribuicao,
                values="Atual (R$)",
                names="Título",
                hole=0.45,
            )
            st.plotly_chart(figura, width="stretch", alt="Distribuição do valor atual por título")

    with st.container(border=True):
        st.subheader("Ranking de desempenho")
        st.caption(
            "Juros semestrais são estimativas de um cupom, calculadas sobre o "
            "valor atual da posição pelas taxas padrão do título. Rendimentos "
            "representam valorização ou desvalorização desde o investimento; "
            "os valores não incluem cupons já recebidos."
        )
        figura = px.bar(
            ranking,
            x="Título",
            y="Rentabilidade (%)",
            color="Rentabilidade (%)",
            color_continuous_scale="RdYlGn",
            labels={"Rentabilidade (%)": "Rentabilidade (%)"},
        )
        st.plotly_chart(figura, width="stretch", alt="Ranking de títulos por rentabilidade")
        st.dataframe(
            ranking.drop(columns=["id"]),
            hide_index=True,
            width="stretch",
            alt="Tabela de desempenho dos títulos da carteira",
            column_config={
                "Investido (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "Atual (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "Lucro (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "Rentabilidade (%)": st.column_config.NumberColumn(format="%.2f%%"),
                "Juros semestrais estimados (R$)": st.column_config.NumberColumn(
                    format="R$ %.2f"
                ),
                "Rendimentos (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
                "Juros + rendimentos (R$)": st.column_config.NumberColumn(
                    format="R$ %.2f"
                ),
            },
        )

    st.subheader("Exportar relatório")
    csv = exportar_csv(ranking.drop(columns=["id"]))
    excel = exportar_excel(service)
    pdf = exportar_pdf(service)
    with st.container(horizontal=True):
        st.download_button(
            "Baixar CSV",
            data=csv,
            file_name="carteira_tesouro.csv",
            mime="text/csv",
            icon=":material/download:",
        )
        st.download_button(
            "Baixar Excel",
            data=excel,
            file_name="carteira_tesouro.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            icon=":material/download:",
        )
        st.download_button(
            "Baixar PDF",
            data=pdf,
            file_name="relatorio_tesouro.pdf",
            mime="application/pdf",
            icon=":material/download:",
        )

st.caption("Os indicadores são informativos e não constituem recomendação de investimento.")

st.header("Cotações do Tesouro Direto")
if not cotacoes:
    st.info(
        "Ainda não há cotações salvas. Use **Atualizar Cotações** para consultar "
        "a página pública; se não houver preços disponíveis, tente novamente "
        "durante o horário de mercado."
    )
else:
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Título": quote.titulo,
                    "Tipo": quote.tipo,
                    "Vencimento": quote.vencimento,
                    "Taxa compra (como publicada)": quote.taxa_compra,
                    "Taxa venda (como publicada)": quote.taxa_venda,
                    "Preço compra (R$)": quote.preco_compra,
                    "Preço venda (R$)": quote.preco_venda,
                    "Data da coleta": quote.data_coleta,
                }
                for quote in cotacoes
            ]
        ),
        hide_index=True,
        width="stretch",
        alt="Últimas cotações públicas do Tesouro Direto",
    )

    cotacao_colunas: list[dict[str, object]] = []
    for quote in cotacoes:
        variacoes = scraper.obter_variacoes(quote.titulo)
        tendencia = scraper.obter_tendencia(quote.titulo)
        cotacao_colunas.append(
            {
                "Título": quote.titulo,
                "Variação diária (%)": variacoes["diaria"],
                "Variação semanal (%)": variacoes["semanal"],
                "Variação mensal (%)": variacoes["mensal"],
                "Variação anual (%)": variacoes["anual"],
                "MM7": tendencia["mm7"],
                "MM21": tendencia["mm21"],
                "MM50": tendencia["mm50"],
                "Tendência": tendencia["tendencia"],
            }
        )
    st.subheader("Variações e tendências")
    st.dataframe(
        pd.DataFrame(cotacao_colunas),
        hide_index=True,
        width="stretch",
        alt="Variações de preço e médias móveis por título",
    )

    comparacao = scraper.comparar_carteira(cotacoes)
    if not comparacao.empty:
        st.subheader("Cotações versus carteira")
        st.dataframe(
            comparacao,
            hide_index=True,
            width="stretch",
            alt="Comparação dos preços atuais com os títulos da carteira",
        )
