"""Testes offline do parser, histórico e fallback do scraper oficial."""

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from database import Database
from models.titulo import Titulo
from services.tesouro_scraper import (
    CotacaoTesouro,
    ScraperError,
    TesouroDiretoScraper,
    categorizar_titulo,
)


ROOT = Path(__file__).parents[1]


HTML_COTACOES = """
<html><body>
<table>
  <thead>
    <tr>
      <th>Título</th><th>Vencimento</th><th>Taxa Compra</th>
      <th>Taxa Venda</th><th>Preço Unitário Compra</th>
      <th>Preço Unitário Venda</th>
    </tr>
  </thead>
  <tbody>
    <tr>
      <td>Tesouro Selic 2029</td><td>01/03/2029</td>
      <td>SELIC + 0,05%</td><td>SELIC + 0,03%</td>
      <td>R$ 15.123,45</td><td>R$ 15.120,00</td>
    </tr>
  </tbody>
</table>
</body></html>
"""


def _database(tmp_path: Path) -> Database:
    """Cria banco temporário usando o esquema real."""
    return Database(
        tmp_path / "tesouro.db",
        schema_path=ROOT / "database" / "schema.sql",
    )


def _quote(
    titulo: str = "Tesouro Selic 2029",
    preco: float = 15120.0,
    data: datetime | None = None,
) -> CotacaoTesouro:
    """Cria uma cotação válida para testes independentes da rede."""
    return CotacaoTesouro(
        titulo=titulo,
        tipo=categorizar_titulo(titulo),
        vencimento="2029-03-01",
        taxa_compra="SELIC + 0,05%",
        taxa_venda="SELIC + 0,03%",
        preco_compra=15123.45,
        preco_venda=preco,
        data_coleta=data or datetime(2026, 10, 23, 18),
    )


def test_categoriza_tipos_de_tesouro() -> None:
    """Reconhece os tipos requeridos e títulos sem classificação."""
    assert categorizar_titulo("Tesouro Selic 2029") == "Tesouro Selic"
    assert categorizar_titulo("Tesouro Prefixado 2032") == "Tesouro Prefixado"
    assert categorizar_titulo("Tesouro IPCA+ 2035") == "Tesouro IPCA+"
    assert categorizar_titulo("Tesouro Renda+ 2060") == "Tesouro Renda+"
    assert categorizar_titulo("Título desconhecido") == "Outros"


def test_parseia_tabela_html_e_valores_brasileiros(tmp_path: Path) -> None:
    """Extrai taxas, vencimento e valores unitários da tabela pública."""
    scraper = TesouroDiretoScraper(database=_database(tmp_path))
    response = Mock()
    response.content = HTML_COTACOES.encode("utf-8")
    response.raise_for_status.return_value = None
    scraper.session.get = Mock(return_value=response)

    cotacoes = scraper.obter_titulos()

    assert len(cotacoes) == 1
    assert cotacoes[0].titulo == "Tesouro Selic 2029"
    assert cotacoes[0].tipo == "Tesouro Selic"
    assert cotacoes[0].vencimento == "2029-03-01"
    assert cotacoes[0].taxa_compra == "SELIC + 0,05%"
    assert cotacoes[0].taxa_venda == "SELIC + 0,03%"
    assert cotacoes[0].preco_compra == pytest.approx(15123.45)
    assert cotacoes[0].preco_venda == pytest.approx(15120.0)


def test_historico_preserva_cada_coleta(tmp_path: Path) -> None:
    """Insere novas linhas sem substituir snapshots do mesmo título."""
    scraper = TesouroDiretoScraper(database=_database(tmp_path))
    primeiro = _quote(data=datetime(2026, 10, 22, 18))
    segundo = _quote(data=datetime(2026, 10, 23, 18), preco=15200)

    assert scraper.salvar_cotacoes([primeiro]) == 1
    assert scraper.salvar_cotacoes([segundo]) == 1
    historico = scraper.obter_historico(primeiro.titulo)

    assert len(historico) == 2
    assert historico["preco_venda"].tolist() == [15120.0, 15200.0]
    assert historico.iloc[-1]["taxa_compra"] == "SELIC + 0,05%"


def test_fallback_usa_ultima_coleta_local_se_scraping_falhar(
    tmp_path: Path,
) -> None:
    """Devolve snapshot salvo em vez de propagar falha HTTP."""
    scraper = TesouroDiretoScraper(database=_database(tmp_path))
    quote = _quote()
    scraper.salvar_cotacoes([quote])
    scraper.session.get = Mock(side_effect=requests.Timeout("timeout"))

    resultado = scraper.atualizar_cotacoes()

    assert resultado.usou_fallback
    assert resultado.erro is not None
    assert resultado.cotacoes[0].titulo == quote.titulo
    assert len(scraper.obter_historico(quote.titulo)) == 1


def test_scraping_sem_tabela_detectavel_falha_com_mensagem_de_html(
    tmp_path: Path,
) -> None:
    """Identifica página sem campos de preço sem inventar cotações."""
    scraper = TesouroDiretoScraper(database=_database(tmp_path))
    response = Mock()
    response.content = b"<html><body>Mercado fechado</body></html>"
    response.raise_for_status.return_value = None
    scraper.session.get = Mock(return_value=response)

    with pytest.raises(ScraperError, match="mercado está fechado"):
        scraper.obter_titulos()


def test_variacoes_e_medias_moveis_com_historico_diario(tmp_path: Path) -> None:
    """Calcula retornos aproximados e tendências de 7, 21 e 50 dias."""
    scraper = TesouroDiretoScraper(database=_database(tmp_path))
    base = datetime(2025, 1, 1, 18)
    cotacoes = [
        _quote(
            data=base + timedelta(days=dia),
            preco=100 + dia,
        )
        for dia in range(60)
    ]
    scraper.salvar_cotacoes(cotacoes)

    variacoes = scraper.obter_variacoes(cotacoes[-1].titulo)
    tendencia = scraper.obter_tendencia(cotacoes[-1].titulo)

    assert variacoes["diaria"] == pytest.approx((159 / 158 - 1) * 100)
    assert variacoes["semanal"] == pytest.approx((159 / 152 - 1) * 100)
    assert variacoes["mensal"] == pytest.approx((159 / 129 - 1) * 100)
    assert variacoes["anual"] is None
    assert tendencia["mm7"] == pytest.approx(156.0)
    assert tendencia["mm21"] == pytest.approx(149.0)
    assert tendencia["mm50"] == pytest.approx(134.5)
    assert tendencia["tendencia"] == "ALTA"


def test_compara_cotacao_atual_com_carteira(tmp_path: Path) -> None:
    """Calcula valor de mercado e ganho contra o preço de compra cadastrado."""
    database = _database(tmp_path)
    database.salvar_titulo(
        Titulo(
            "Tesouro Selic 2029",
            "Tesouro Selic",
            datetime(2025, 1, 1).date(),
            2,
            14000,
        )
    )
    scraper = TesouroDiretoScraper(database=database)

    resultado = scraper.comparar_carteira([_quote(preco=15000)])

    assert len(resultado) == 1
    linha = resultado.iloc[0]
    assert linha["Situação"] == "Atualizado"
    assert linha["Investido (R$)"] == 28000
    assert linha["Atual (R$)"] == 30000
    assert linha["Lucro / prejuízo (R$)"] == 2000
    assert linha["Rentabilidade (%)"] == pytest.approx((30000 / 28000 - 1) * 100)
