"""Interface para importar e auditar arquivos XLSX de corretoras."""

import logging

import pandas as pd
import streamlit as st

from config import BASE_DIR, DATABASE_PATH
from database.repository import Database
from modules.tesouro.services.importador_xlsx import (
    importar_planilhas_corretora,
)

logger = logging.getLogger(__name__)
pasta_xlsx = BASE_DIR / "xlsx"
pasta_xlsx.mkdir(parents=True, exist_ok=True)
arquivos = sorted(
    caminho.name
    for caminho in pasta_xlsx.glob("*.xlsx")
    if caminho.is_file() and not caminho.name.startswith("~$")
)

st.title("Importação de posições")
st.caption(
    "Importa os arquivos .xlsx da pasta `xlsx` e consolida posições por ativo. "
    "Arquivos com o mesmo conteúdo não são importados novamente."
)

if arquivos:
    st.subheader("Arquivos encontrados")
    st.dataframe(
        pd.DataFrame({"Arquivo": arquivos}),
        hide_index=True,
        width="stretch",
        alt="Arquivos Excel encontrados na pasta xlsx",
    )
else:
    st.info(f"Nenhum arquivo .xlsx encontrado em `{pasta_xlsx}`.")

if st.button(
    "Importar Arquivos XLSX",
    type="primary",
    icon=":material/upload_file:",
    disabled=not arquivos,
):
    barra_progresso = st.progress(0, text="Preparando importação...")
    estado = st.empty()

    def atualizar_progresso(atual: int, total: int, nome: str) -> None:
        """Atualiza o progresso por arquivo durante a importação."""
        valor = atual / total if total else 1.0
        barra_progresso.progress(
            valor,
            text=f"Processando {nome} ({atual}/{total})",
        )
        estado.caption(f"Arquivo atual: {nome}")

    try:
        relatorio = importar_planilhas_corretora(
            pasta=pasta_xlsx,
            database=Database(DATABASE_PATH),
            progresso=atualizar_progresso,
        )
    except (OSError, RuntimeError, ValueError) as erro:
        logger.exception("Importação XLSX não concluída.")
        st.error(f"Não foi possível concluir a importação: {erro}")
    else:
        barra_progresso.progress(1.0, text="Importação concluída.")
        if relatorio.novos_ativos or relatorio.ativos_atualizados:
            st.success(
                "As posições foram incluídas na carteira e já aparecem no dashboard."
            )
        st.code(relatorio.formatar(), language=None)
        if relatorio.erros:
            st.warning(
                f"{len(relatorio.erros)} arquivo(s) apresentaram problemas. "
                "Consulte os detalhes abaixo e os logs da aplicação."
            )
            for erro in relatorio.erros:
                st.error(erro)
        if relatorio.posicoes:
            st.subheader("Posições consolidadas nesta importação")
            st.dataframe(
                pd.DataFrame(
                    [
                        {
                            "Ticker": posicao.ticker,
                            "Ativo": posicao.nome,
                            "Categoria": posicao.categoria,
                            "Quantidade": posicao.quantidade,
                            "Preço médio (R$)": posicao.preco_medio,
                            "Valor investido (R$)": posicao.valor_investido,
                            "Vencimento": posicao.vencimento,
                        }
                        for posicao in relatorio.posicoes
                    ]
                ),
                hide_index=True,
                width="stretch",
                alt="Posições consolidadas a partir dos arquivos importados",
            )

st.subheader("Histórico de importações")
try:
    historico = Database(DATABASE_PATH).listar_importacoes()
except RuntimeError as erro:
    logger.exception("Falha ao exibir histórico de importações.")
    st.error(str(erro))
else:
    if historico:
        st.dataframe(
            pd.DataFrame(historico),
            hide_index=True,
            width="stretch",
            alt="Histórico de arquivos XLSX importados",
            column_config={
                "arquivo": st.column_config.TextColumn("Arquivo"),
                "data_importacao": st.column_config.TextColumn(
                    "Data da importação"
                ),
                "linhas_processadas": st.column_config.NumberColumn(
                    "Linhas processadas"
                ),
                "status": st.column_config.TextColumn("Status"),
                "detalhes": st.column_config.TextColumn("Detalhes"),
            },
        )
    else:
        st.caption("Ainda não há importações registradas.")

st.subheader("Carteira importada")
try:
    ativos_importados = Database(DATABASE_PATH).listar_ativos()
except RuntimeError as erro:
    logger.exception("Falha ao exibir ativos importados.")
    st.error(str(erro))
else:
    if ativos_importados:
        st.dataframe(
            pd.DataFrame(ativos_importados),
            hide_index=True,
            width="stretch",
            alt="Posições importadas e consolidadas no banco de dados",
            column_config={
                "ticker": st.column_config.TextColumn("Ticker"),
                "nome": st.column_config.TextColumn("Ativo"),
                "categoria": st.column_config.TextColumn("Categoria"),
                "quantidade": st.column_config.NumberColumn("Quantidade"),
                "preco_medio": st.column_config.NumberColumn(
                    "Preço médio", format="R$ %.2f"
                ),
                "valor_investido": st.column_config.NumberColumn(
                    "Valor investido", format="R$ %.2f"
                ),
                "vencimento": st.column_config.TextColumn("Vencimento"),
                "data_atualizacao": st.column_config.TextColumn(
                    "Atualizado em"
                ),
            },
        )
    else:
        st.caption("Nenhuma posição foi importada ainda.")

st.caption(
    "A importação é aditiva: arquivos novos contendo o mesmo ativo são "
    "consolidados com preço médio ponderado. Revise arquivos antes de importar "
    "caso sejam extratos repetidos da mesma posição."
)
