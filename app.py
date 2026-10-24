"""Ponto de entrada do dashboard multipágina."""

import logging

import streamlit as st

from database import Database

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

st.set_page_config(
    page_title="Tesouro Tracker",
    page_icon=":material/account_balance:",
    layout="wide",
)

try:
    Database()
except RuntimeError as erro:
    st.error(str(erro), icon=":material/error:")
    st.stop()

pagina = st.navigation(
    [
        st.Page(
            "pages/dashboard.py",
            title="Dashboard",
            icon=":material/dashboard:",
            default=True,
        ),
        st.Page(
            "pages/carteira.py",
            title="Carteira",
            icon=":material/account_balance_wallet:",
        ),
        st.Page(
            "pages/analise.py",
            title="Análise",
            icon=":material/query_stats:",
        ),
        st.Page(
            "pages/importacao.py",
            title="Importação XLSX",
            icon=":material/upload_file:",
        ),
    ],
    position="top",
)
pagina.run()
