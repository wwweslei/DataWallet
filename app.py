"""Ponto de entrada do dashboard multipágina."""

import logging

import streamlit as st

from database.repository import Database
from modules.navigation import get_pages

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

pagina = st.navigation(get_pages(), position="top")
pagina.run()
