"""Cadastro, edição, remoção e atualização manual da carteira."""

import logging
from datetime import date

import streamlit as st

from config import DATABASE_PATH, TIPOS_TITULO
from database import Database
from models.titulo import Titulo
from services.carteira import CarteiraService
from utils.helpers import formatar_moeda

logger = logging.getLogger(__name__)
database = Database(DATABASE_PATH)
service = CarteiraService(database)
titulos = database.listar_titulos()

st.title("Gestão da carteira")
st.caption("Cadastre posições e registre cotações para manter o histórico atualizado.")

aba_novo, aba_editar, aba_cotacao = st.tabs(
    ["Cadastrar título", "Editar / remover", "Registrar preço"]
)

with aba_novo:
    with st.form("novo_titulo", clear_on_submit=True):
        nome = st.text_input("Nome do título", placeholder="Ex.: Tesouro IPCA+ 2035")
        tipo = st.selectbox("Tipo do título", TIPOS_TITULO)
        data_compra = st.date_input(
            "Data de compra", value=date.today(), max_value=date.today()
        )
        quantidade = st.number_input(
            "Quantidade",
            min_value=0.000001,
            value=1.0,
            step=0.1,
            format="%.6f",
        )
        preco_compra = st.number_input(
            "Preço de compra por unidade (R$)",
            min_value=0.01,
            value=1000.0,
            step=10.0,
        )
        preco_atual = st.number_input(
            "Preço atual por unidade (R$)",
            min_value=0.0,
            value=0.0,
            step=10.0,
            help="Deixe zero se ainda não tiver uma cotação.",
        )
        enviar = st.form_submit_button("Cadastrar", type="primary")
    if enviar:
        try:
            titulo = Titulo(
                nome=nome,
                tipo=tipo,
                data_compra=data_compra,
                quantidade=quantidade,
                preco_compra=preco_compra,
                preco_atual=preco_atual if preco_atual > 0 else None,
            )
            database.salvar_titulo(titulo)
            st.success("Título cadastrado.")
            st.rerun()
        except (ValueError, RuntimeError) as erro:
            logger.warning("Cadastro de título não concluído: %s", erro)
            st.error(str(erro))

with aba_editar:
    if not titulos:
        st.info("Cadastre um título para habilitar edição.")
    else:
        titulo_selecionado = st.selectbox(
            "Título",
            titulos,
            format_func=lambda item: f"{item.nome} — {item.tipo}",
            key="titulo_editar",
        )
        with st.form("editar_titulo"):
            nome_editado = st.text_input("Nome", value=titulo_selecionado.nome)
            tipo_editado = st.selectbox(
                "Tipo",
                TIPOS_TITULO,
                index=TIPOS_TITULO.index(titulo_selecionado.tipo)
                if titulo_selecionado.tipo in TIPOS_TITULO
                else TIPOS_TITULO.index("Outro"),
            )
            data_editada = st.date_input(
                "Data de compra",
                value=titulo_selecionado.data_compra,
                max_value=date.today(),
            )
            quantidade_editada = st.number_input(
                "Quantidade",
                min_value=0.000001,
                value=float(titulo_selecionado.quantidade),
                step=0.1,
                format="%.6f",
            )
            preco_compra_editado = st.number_input(
                "Preço de compra por unidade (R$)",
                min_value=0.01,
                value=float(titulo_selecionado.preco_compra),
                step=10.0,
            )
            preco_atual_editado = st.number_input(
                "Preço atual por unidade (R$)",
                min_value=0.0,
                value=float(titulo_selecionado.preco_atual or 0),
                step=10.0,
                help="Deixe zero para manter a última cotação registrada no histórico.",
            )
            salvar = st.form_submit_button("Salvar alterações", type="primary")
        if salvar:
            try:
                database.salvar_titulo(
                    Titulo(
                        id=titulo_selecionado.id,
                        nome=nome_editado,
                        tipo=tipo_editado,
                        data_compra=data_editada,
                        quantidade=quantidade_editada,
                        preco_compra=preco_compra_editado,
                        preco_atual=preco_atual_editado or None,
                    )
                )
                st.success("Título atualizado.")
                st.rerun()
            except (ValueError, RuntimeError, LookupError) as erro:
                logger.warning("Edição de título não concluída: %s", erro)
                st.error(str(erro))
        confirmar = st.checkbox("Confirmo a remoção do título e de seu histórico")
        if st.button("Remover título", type="secondary", disabled=not confirmar):
            try:
                database.remover_titulo(titulo_selecionado.id or 0)
                st.success("Título removido.")
                st.rerun()
            except (RuntimeError, LookupError) as erro:
                logger.exception("Remoção do título não concluída.")
                st.error(str(erro))

with aba_cotacao:
    if not titulos:
        st.info("Cadastre um título antes de registrar cotações.")
    else:
        titulo_cotacao = st.selectbox(
            "Título para cotação",
            titulos,
            format_func=lambda item: f"{item.nome} — {item.tipo}",
            key="titulo_cotacao",
        )
        with st.form("registrar_cotacao"):
            data_cotacao = st.date_input(
                "Data da cotação", value=date.today(), max_value=date.today()
            )
            preco_cotacao = st.number_input(
                "Preço por unidade (R$)",
                min_value=0.01,
                value=float(
                    titulo_cotacao.preco_atual or titulo_cotacao.preco_compra
                ),
                step=1.0,
            )
            salvar_cotacao = st.form_submit_button("Salvar cotação")
        if salvar_cotacao:
            try:
                database.registrar_preco(
                    titulo_cotacao.id or 0,
                    data_cotacao,
                    preco_cotacao,
                )
                st.success("Cotação registrada no histórico.")
                st.rerun()
            except (ValueError, RuntimeError, LookupError) as erro:
                logger.warning("Registro de cotação não concluído: %s", erro)
                st.error(str(erro))

st.subheader("Posições atuais")
ranking = service.obter_ranking()
if ranking.empty:
    st.info("Nenhuma posição cadastrada.")
else:
    st.dataframe(
        ranking.drop(columns=["id"]),
        hide_index=True,
        width="stretch",
        alt="Posições registradas na carteira",
        column_config={
            "Investido (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Atual (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Lucro (R$)": st.column_config.NumberColumn(format="R$ %.2f"),
            "Rentabilidade (%)": st.column_config.NumberColumn(format="%.2f%%"),
        },
    )
    st.caption(
        f"Investido: {formatar_moeda(service.obter_resumo()['valor_investido'])} "
        f"· Valor atual: {formatar_moeda(service.obter_resumo()['valor_atual'])}"
    )
