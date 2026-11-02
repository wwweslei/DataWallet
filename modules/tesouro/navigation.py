"""Páginas Streamlit do módulo Tesouro."""

import streamlit as st


def get_pages():
    """Registra as páginas atuais do Tesouro na navegação da aplicação."""
    return [
        st.Page(
            "modules/tesouro/pages/dashboard.py",
            title="Dashboard",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page(
            "modules/tesouro/pages/carteira.py",
            title="Carteira",
            icon=":material/account_balance_wallet:",
        ),
        st.Page(
            "modules/tesouro/pages/analise.py",
            title="Análise",
            icon=":material/query_stats:",
        ),
        st.Page(
            "modules/tesouro/pages/importacao.py",
            title="Importação XLSX",
            icon=":material/upload_file:",
        ),
    ]
