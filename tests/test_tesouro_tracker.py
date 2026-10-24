"""Testes do modelo, banco e cálculos financeiros do Tesouro Tracker."""

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from unittest.mock import Mock

from database import Database
from models.titulo import Titulo
from services.indicadores import IndicadoresService
from services.tendencias import TendenciasService
from services.carteira import CarteiraService
from services.data_provider import JsonHttpTesouroProvider, atualizar_cotacoes
from utils.helpers import exportar_csv, exportar_excel, exportar_pdf


def _database(tmp_path: Path) -> Database:
    """Cria um banco temporário usando o esquema real do projeto."""
    return Database(
        tmp_path / "tesouro.db",
        schema_path=Path(__file__).parents[1] / "database" / "schema.sql",
    )


def test_titulo_calcula_valores_e_rejeita_valores_invalidos() -> None:
    """Garante o cálculo de posição e validação de preço."""
    titulo = Titulo("Tesouro Selic", "Tesouro Selic", date(2025, 1, 1), 2, 100.0, preco_atual=105.0)
    assert titulo.valor_investido == 200.0
    assert titulo.valor_atual == 210.0
    with pytest.raises(ValueError, match="maior que zero"):
        Titulo("Inválido", "Outro", date.today(), 0, 100.0)


def test_sqlite_persiste_posicao_e_historico_e_remove_em_cascata(tmp_path) -> None:
    """Verifica CRUD, cotação diária e remoção do histórico dependente."""
    database = _database(tmp_path)
    titulo_id = database.salvar_titulo(
        Titulo("Tesouro IPCA+ 2035", "Tesouro IPCA+", date(2025, 2, 1), 1.5, 1000.0)
    )
    database.registrar_preco(titulo_id, date(2025, 2, 2), 1010.0)

    titulos = database.listar_titulos()
    assert len(titulos) == 1
    assert titulos[0].id == titulo_id
    assert titulos[0].valor_atual == pytest.approx(1515.0)
    assert database.obter_historico(titulo_id)[0]["preco"] == pytest.approx(1010.0)

    database.remover_titulo(titulo_id)
    assert database.listar_titulos() == []
    assert database.obter_historico(titulo_id) == []


def test_indicadores_tecnicos_e_metricas_performance() -> None:
    """Calcula indicadores técnicos e métricas em série crescente."""
    precos = pd.Series(np.linspace(100, 180, 260))
    indicadores = IndicadoresService.indicadores_tecnicos(precos)
    performance = IndicadoresService.metricas_performance(precos)

    assert indicadores["mm9"].notna().sum() == 252
    assert indicadores["mm200"].notna().sum() == 61
    assert indicadores["rsi"].iloc[-1] == pytest.approx(100.0)
    assert performance["retorno_total"] == pytest.approx(80.0)
    assert performance["drawdown_maximo"] == pytest.approx(0.0)
    assert performance["volatilidade"] > 0


def test_sinal_de_venda_quando_rsi_sobrecomprado() -> None:
    """Garante que a regra de sobrecompra gera sinal de venda."""
    indicadores = pd.DataFrame(
        [
            {
                "mm9": 110.0,
                "mm21": 100.0,
                "rsi": 75.0,
                "macd": 2.0,
                "macd_sinal": 1.0,
                "roc": 1.0,
            }
        ]
    )
    resultado = TendenciasService.analisar(indicadores, volatilidade=10.0)
    assert resultado["tendencia"] == "Tendência de Alta"
    assert resultado["rsi_condicao"] == "Sobrecomprado"
    assert resultado["sinal"] == "Sinal de Venda"
    assert 0 <= resultado["score_tendencia"] <= 100
    assert 0 <= resultado["score_risco"] <= 100


def test_previsao_linear_valida_entrada_e_retorna_periodos() -> None:
    """Confere extrapolação simples e validação da quantidade de períodos."""
    resultado = IndicadoresService.previsao_linear([10, 12, 14], periodos=2)
    assert resultado.to_list() == pytest.approx([16.0, 18.0])
    with pytest.raises(ValueError, match="positiv"):
        IndicadoresService.previsao_linear([10, 12], periodos=0)


def test_retornos_diario_semanal_mensal_e_anual_usa_dados_disponiveis() -> None:
    """Calcula variações trailing para séries mais curtas que um ano."""
    datas = pd.date_range(date.today() - timedelta(days=29), periods=30, freq="D")
    serie = pd.Series(np.arange(100.0, 130.0), index=datas)
    metricas = IndicadoresService.metricas_performance(serie)
    assert metricas["retorno_diario"] == pytest.approx((129 / 128 - 1) * 100)
    assert metricas["retorno_semanal"] == pytest.approx((129 / 124 - 1) * 100)
    assert metricas["retorno_mensal"] == pytest.approx((129 / 108 - 1) * 100)
    assert metricas["retorno_anual"] == pytest.approx((129 / 100 - 1) * 100)


def test_provedor_json_atualiza_cotacao_do_titulo(monkeypatch, tmp_path: Path) -> None:
    """Normaliza o contrato JSON e salva a cotação no SQLite."""
    resposta = Mock()
    resposta.json.return_value = [
        {
            "nome_titulo": "Tesouro Selic",
            "data": "2025-03-01",
            "preco": 102.5,
        }
    ]
    resposta.raise_for_status.return_value = None
    monkeypatch.setattr("services.data_provider.requests.get", Mock(return_value=resposta))
    database = _database(tmp_path)
    titulo_id = database.salvar_titulo(
        Titulo("tesouro selic", "Tesouro Selic", date(2025, 1, 1), 1, 100.0)
    )

    quantidade = atualizar_cotacoes(
        database, JsonHttpTesouroProvider("https://quotes.example.test")
    )

    assert quantidade == 1
    assert database.obter_historico(titulo_id)[0]["preco"] == pytest.approx(102.5)
    resposta.raise_for_status.assert_called_once_with()


def test_exportadores_criam_csv_excel_e_pdf(tmp_path: Path) -> None:
    """Valida os três formatos de exportação para uma carteira preenchida."""
    database = _database(tmp_path)
    database.salvar_titulo(
        Titulo(
            "Tesouro IPCA+ 2035",
            "Tesouro IPCA+",
            date(2025, 1, 1),
            1,
            1000,
            preco_atual=1100,
        )
    )
    service = CarteiraService(database)
    ranking = service.obter_ranking()

    assert exportar_csv(ranking).startswith(b"\xef\xbb\xbf")
    assert exportar_excel(service).startswith(b"PK")
    assert exportar_pdf(service).startswith(b"%PDF")
