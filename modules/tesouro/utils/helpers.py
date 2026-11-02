"""Formatação e exportação de relatórios da carteira."""

from __future__ import annotations

from datetime import date
from io import BytesIO

import pandas as pd
from fpdf import FPDF

from modules.tesouro.services.carteira import CarteiraService


def formatar_moeda(valor: float) -> str:
    """Formata um valor numérico como reais no padrão brasileiro."""
    return f"R$ {valor:,.2f}".replace(",", "_").replace(".", ",").replace("_", ".")


def exportar_csv(ranking: pd.DataFrame) -> bytes:
    """Gera uma exportação CSV compatível com planilhas brasileiras."""
    return ranking.to_csv(index=False, decimal=",", sep=";").encode("utf-8-sig")


def exportar_excel(service: CarteiraService) -> bytes:
    """Gera um arquivo Excel com posição, evolução e preços históricos."""
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        ranking = service.obter_ranking()
        ranking.drop(columns=["id"], errors="ignore").to_excel(
            writer, sheet_name="Carteira", index=False
        )
        service.obter_evolucao().to_excel(
            writer, sheet_name="Evolução", index=False
        )
        for titulo in service.database.listar_titulos():
            historico = pd.DataFrame(
                service.database.obter_historico(titulo.id or 0)
            )
            if not historico.empty:
                nome_aba = f"Hist {titulo.id}"[:31]
                historico.to_excel(writer, sheet_name=nome_aba, index=False)
    return buffer.getvalue()


def exportar_pdf(service: CarteiraService) -> bytes:
    """Cria um relatório PDF com posições, desempenho e análise técnica."""
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", style="B", size=18)
    pdf.cell(0, 12, "Relatorio Tesouro Tracker", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=10)
    pdf.cell(0, 8, f"Gerado em {date.today().strftime('%d/%m/%Y')}", new_x="LMARGIN", new_y="NEXT")
    resumo = service.obter_resumo()
    for rotulo, chave in (
        ("Valor investido", "valor_investido"),
        ("Valor atual", "valor_atual"),
        ("Lucro / prejuizo", "lucro"),
    ):
        pdf.cell(
            0,
            7,
            f"{rotulo}: {formatar_moeda(resumo[chave])}",
            new_x="LMARGIN",
            new_y="NEXT",
        )
    pdf.cell(
        0,
        7,
        f"Rentabilidade total: {resumo['rentabilidade']:.2f}%",
        new_x="LMARGIN",
        new_y="NEXT",
    )
    pdf.ln(4)
    pdf.set_font("Helvetica", style="B", size=11)
    pdf.cell(0, 8, "Titulos da carteira", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", size=8)
    for titulo in service.database.listar_titulos():
        indicadores, tendencia, performance = service.obter_analise(titulo)
        historico = service.database.obter_historico(titulo.id or 0)
        pdf.set_font("Helvetica", style="B", size=11)
        pdf.multi_cell(
            0,
            7,
            f"{titulo.nome} - {titulo.tipo}".encode("latin-1", "replace").decode("latin-1"),
            new_x="LMARGIN",
            new_y="NEXT",
        )
        pdf.set_font("Helvetica", size=8)
        campos = (
            f"Compra: {titulo.data_compra.strftime('%d/%m/%Y')} | "
            f"Quantidade: {titulo.quantidade:g} | "
            f"Preco de compra: {formatar_moeda(titulo.preco_compra)}",
            f"Investido: {formatar_moeda(titulo.valor_investido)} | "
            f"Atual: {formatar_moeda(titulo.valor_atual)} | "
            f"Lucro/prejuizo: {formatar_moeda(titulo.valor_atual - titulo.valor_investido)}",
            f"Retornos (%): acumulado {performance['retorno_total']:.2f}, "
            f"diario {performance['retorno_diario']:.2f}, "
            f"semanal {performance['retorno_semanal']:.2f}, "
            f"mensal {performance['retorno_mensal']:.2f}, "
            f"anual {performance['retorno_anual']:.2f}",
            f"Drawdown maximo: {performance['drawdown_maximo']:.2f}% | "
            f"Volatilidade anualizada: {performance['volatilidade']:.2f}%",
            f"Tendencia: {tendencia['tendencia']} | RSI: {tendencia['rsi_condicao']} | "
            f"Sinal: {tendencia['sinal']} | "
            f"Score de tendencia: {tendencia['score_tendencia']:.0f}/100 | "
            f"Score de risco: {tendencia['score_risco']:.0f}/100",
        )
        for campo in campos:
            pdf.multi_cell(
                0,
                5,
                campo.encode("latin-1", "replace").decode("latin-1"),
                new_x="LMARGIN",
                new_y="NEXT",
            )
        if not indicadores.empty:
            ultima = indicadores.iloc[-1]
            valores = [
                f"MM{periodo}: {ultima[f'mm{periodo}']:.2f}"
                for periodo in (9, 21, 50, 200)
                if pd.notna(ultima[f"mm{periodo}"])
            ]
            valores.extend(
                f"{nome}: {ultima[coluna]:.2f}"
                for coluna, nome in (
                    ("macd", "MACD"),
                    ("macd_sinal", "MACD sinal"),
                    ("bollinger_superior", "Bollinger sup"),
                    ("bollinger_inferior", "Bollinger inf"),
                    ("roc", "ROC"),
                )
                if pd.notna(ultima[coluna])
            )
            if valores:
                texto = "Indicadores: " + " | ".join(valores)
                pdf.multi_cell(
                    0,
                    5,
                    texto.encode("latin-1", "replace").decode("latin-1"),
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
        if historico:
            pdf.multi_cell(
                0, 5, "Cotações recentes:", new_x="LMARGIN", new_y="NEXT"
            )
            for item in historico[-10:]:
                pdf.cell(
                    0,
                    5,
                    f"{item['data']}: {formatar_moeda(float(item['preco']))} ({item['fonte']})",
                    new_x="LMARGIN",
                    new_y="NEXT",
                )
        pdf.ln(3)
    pdf.ln(4)
    pdf.set_font("Helvetica", size=8)
    pdf.multi_cell(
        0,
        5,
        "Indicadores historicos e previsoes sao estimativas baseadas em dados "
        "registrados pelo usuario; nao constituem recomendacao de investimento.",
    )
    resultado = pdf.output()
    return bytes(resultado)
