"""Testes do mapeamento, consolidação e importação de planilhas XLSX."""

from datetime import date
from pathlib import Path

import pytest
from openpyxl import Workbook

from database.repository import Database
from modules.tesouro.services.consolidador import (
    PosicaoImportada,
    consolidar_posicoes,
)
from modules.tesouro.services.carteira import CarteiraService
from modules.tesouro.services.importador_xlsx import (
    _numero,
    importar_planilhas_corretora,
)
from modules.tesouro.services.mapeador_colunas import mapear_colunas


ROOT = Path(__file__).parents[1]


def _database(tmp_path: Path) -> Database:
    """Cria um banco de teste com o esquema de produção."""
    return Database(
        tmp_path / "tesouro.db",
        schema_path=ROOT / "database" / "schema.sql",
    )


def _criar_planilha(
    caminho: Path,
    linhas: list[list[object]],
) -> None:
    """Grava linhas de teste em um arquivo Excel."""
    workbook = Workbook()
    sheet = workbook.active
    for linha in linhas:
        sheet.append(linha)
    workbook.save(caminho)
    workbook.close()


def test_mapeador_reconhece_aliases_de_corretoras() -> None:
    """Mapeia nomes comuns e variações de cabeçalho."""
    resultado = mapear_colunas(
        [
            "Código",
            "Quantidade Atual",
            "Preço Médio",
            "Valor Investido",
            "Data Operação",
            "Tipo do Ativo",
        ]
    )

    assert resultado == {
        "ticker": 0,
        "quantidade": 1,
        "preco_medio": 2,
        "valor_investido": 3,
        "data_compra": 4,
        "categoria": 5,
    }


@pytest.mark.parametrize(
    ("tipo", "categoria"),
    [("Ação", "Ação"), ("FII", "FII"), ("ETF", "ETF"), ("BDR", "BDR")],
)
def test_importa_classes_de_renda_variavel(
    tipo: str,
    categoria: str,
    tmp_path: Path,
) -> None:
    """Preserva a classe de renda variável informada pelo extrato."""
    pasta = tmp_path / "xlsx"
    pasta.mkdir()
    _criar_planilha(
        pasta / "ativos.xlsx",
        [
            ["Ativo", "Quantidade", "PM", "Valor Investido", "Tipo"],
            ["ABCD11", 2, 25, 50, tipo],
        ],
    )

    relatorio = importar_planilhas_corretora(
        pasta=pasta,
        database=_database(tmp_path),
    )

    assert relatorio.posicoes[0].categoria == categoria


@pytest.mark.parametrize(
    ("entrada", "esperado"),
    [
        ("R$ 1.234,56", 1234.56),
        ("1,234.56", 1234.56),
        ("0,50", 0.5),
        ("(1.234,56)", -1234.56),
        ("", None),
        ("texto inválido", None),
    ],
)
def test_parse_numeros_brasileiros(
    entrada: str, esperado: float | None
) -> None:
    """Interpreta valores com vírgula decimal, separadores e moeda."""
    assert _numero(entrada) == esperado


def test_consolidacao_calcula_preco_medio_ponderado() -> None:
    """Agrupa ativos repetidos pelo ticker e pondera o preço pelo custo."""
    resultado = consolidar_posicoes(
        [
            PosicaoImportada("petr4", "Petrobras", "Ação", 100, 30, 3000),
            PosicaoImportada("PETR4", "PETR4", "Outro", 50, 32, 1600),
        ]
    )

    assert len(resultado) == 1
    assert resultado[0].ticker == "PETR4"
    assert resultado[0].quantidade == 150
    assert resultado[0].valor_investido == 4600
    assert resultado[0].preco_medio == pytest.approx(4600 / 150)
    assert resultado[0].categoria == "Ação"


def test_importa_arquivos_novos_e_nao_duplica_em_reexecucao(
    tmp_path: Path,
) -> None:
    """Importa vários arquivos, consolida posições e ignora hashes repetidos."""
    pasta = tmp_path / "xlsx"
    pasta.mkdir()
    _criar_planilha(
        pasta / "extrato_a.xlsx",
        [
            ["Código", "Nome do Ativo", "Quantidade Atual", "Preço Médio", "Valor Investido"],
            ["PETR4", "Petrobras PN", 100, 30, 3000],
        ],
    )
    _criar_planilha(
        pasta / "extrato_b.xlsx",
        [
            ["Ticker", "Produto", "Posição", "PM", "Valor Aplicado"],
            ["PETR4", "Petrobras PN", 50, 32, 1600],
        ],
    )
    database = _database(tmp_path)

    primeiro = importar_planilhas_corretora(pasta=pasta, database=database)

    assert primeiro.arquivos_processados == 2
    assert primeiro.ativos_encontrados == 1
    assert primeiro.novos_ativos == 1
    assert primeiro.ativos_atualizados == 0
    assert primeiro.valor_total_importado == 4600
    ativo = database.listar_ativos()[0]
    assert ativo["ticker"] == "PETR4"
    assert ativo["quantidade"] == 150
    assert ativo["preco_medio"] == pytest.approx(4600 / 150)
    assert len(database.listar_importacoes()) == 2

    segundo = importar_planilhas_corretora(pasta=pasta, database=database)
    assert segundo.arquivos_processados == 0
    assert segundo.arquivos_ignorados == 2
    assert database.listar_ativos()[0]["quantidade"] == 150


def test_posicoes_importadas_aparecem_no_resumo_ranking_e_evolucao(
    tmp_path: Path,
) -> None:
    """Inclui posições importadas nos valores consumidos pelo dashboard."""
    pasta = tmp_path / "xlsx"
    pasta.mkdir()
    _criar_planilha(
        pasta / "carteira.xlsx",
        [
            ["Ticker", "Nome", "Categoria", "Quantidade", "Preço Médio"],
            ["PETR4", "Petrobras PN", "Ação", 10, 30],
        ],
    )
    database = _database(tmp_path)
    importar_planilhas_corretora(pasta=pasta, database=database)

    service = CarteiraService(database)
    resumo = service.obter_resumo()
    ranking = service.obter_ranking()
    evolucao = service.obter_evolucao()

    assert resumo["valor_investido"] == 300
    assert resumo["valor_atual"] == 300
    assert len(ranking) == 1
    assert ranking.iloc[0]["Título"] == "Petrobras PN"
    assert ranking.iloc[0]["Situação"] == (
        "Cotação indisponível; usando valor investido"
    )
    assert len(evolucao) == 1
    assert evolucao.iloc[0]["valor"] == 300


def test_importacao_posterior_atualiza_quantidade_e_preco_medio(
    tmp_path: Path,
) -> None:
    """Acrescenta uma posição importada depois e atualiza preço médio."""
    pasta = tmp_path / "xlsx"
    pasta.mkdir()
    database = _database(tmp_path)
    primeiro = pasta / "posicao_1.xlsx"
    _criar_planilha(
        primeiro,
        [["Ativo", "Quantidade", "Preço Médio"], ["VALE3", 10, 50]],
    )
    importar_planilhas_corretora(pasta=pasta, database=database)
    _criar_planilha(
        pasta / "posicao_2.xlsx",
        [["Ativo", "Quantidade", "Preço Médio"], ["VALE3", 5, 60]],
    )

    relatorio = importar_planilhas_corretora(pasta=pasta, database=database)

    ativo = database.listar_ativos()[0]
    assert relatorio.arquivos_processados == 1
    assert relatorio.novos_ativos == 0
    assert relatorio.ativos_atualizados == 1
    assert ativo["quantidade"] == 15
    assert ativo["valor_investido"] == 800
    assert ativo["preco_medio"] == pytest.approx(800 / 15)


def test_reserva_de_importacao_impede_processamento_concorrente(
    tmp_path: Path,
) -> None:
    """Não permite que dois processos importem o mesmo conteúdo ao mesmo tempo."""
    database = _database(tmp_path)

    assert database.iniciar_importacao("posicao.xlsx", "hash-1")
    assert not database.iniciar_importacao("posicao.xlsx", "hash-1")
    database.registrar_importacao("posicao.xlsx", "hash-1", "erro", detalhes="teste")
    assert database.iniciar_importacao("posicao.xlsx", "hash-1")
    database.registrar_importacao("posicao.xlsx", "hash-1", "sucesso")
    assert not database.iniciar_importacao("posicao.xlsx", "hash-1")


def test_importa_extrato_tesouro_com_cabecalho_em_duas_linhas(
    tmp_path: Path,
) -> None:
    """Lê os cabeçalhos agrupados dos extratos analíticos do Tesouro Direto."""
    pasta = tmp_path / "xlsx"
    pasta.mkdir()
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "Extrato Analítico"
    sheet["A1"] = "EXTRATO ANALÍTICO - Tesouro Selic 2027"
    sheet["A2"] = "INVESTIDOR: DADO OMITIDO"
    sheet["A4"] = "VENCIMENTO: 01/03/2027"
    sheet["A6"] = "Data da aplicação"
    sheet["B6"] = "Quantidade de títulos A"
    sheet["C6"] = "Preço do título na aplicação (R$) B"
    sheet["D6"] = "Valor investido (R$) A x B"
    sheet["A7"] = "Data da aplicação"
    sheet["B7"] = "Quantidade de títulos A"
    sheet["C7"] = "Preço do título na aplicação (R$) B"
    sheet["D7"] = "Valor investido (R$) A x B"
    sheet["A8"] = date(2025, 1, 2)
    sheet["B8"] = "0,50"
    sheet["C8"] = "11.341,37"
    sheet["D8"] = "5.670,68"
    sheet["A9"] = "Total"
    sheet["B9"] = "0,50"
    sheet["C9"] = "11.341,37"
    sheet["D9"] = "5.670,68"
    workbook.save(pasta / "selic27.xlsx")
    workbook.close()
    database = _database(tmp_path)

    relatorio = importar_planilhas_corretora(pasta=pasta, database=database)

    assert relatorio.arquivos_processados == 1
    ativo = database.listar_ativos()[0]
    assert ativo["ticker"] == "TESOURO SELIC 2027"
    assert ativo["categoria"] == "Tesouro Selic"
    assert ativo["quantidade"] == pytest.approx(0.5)
    assert ativo["preco_medio"] == pytest.approx(11341.37)
    assert ativo["valor_investido"] == pytest.approx(5670.68)
    assert ativo["vencimento"] == "2027-03-01"


def test_database_migra_tabela_ativos_existente(tmp_path: Path) -> None:
    """Adiciona a coluna de vencimento a bancos criados por versão anterior."""
    import sqlite3

    caminho = tmp_path / "tesouro.db"
    with sqlite3.connect(caminho) as conexao:
        conexao.execute(
            """CREATE TABLE ativos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker TEXT NOT NULL UNIQUE,
                nome TEXT NOT NULL,
                categoria TEXT NOT NULL,
                quantidade REAL NOT NULL,
                preco_medio REAL NOT NULL,
                valor_investido REAL NOT NULL,
                data_atualizacao TEXT NOT NULL
            )"""
        )

    _database(tmp_path)
    with sqlite3.connect(caminho) as conexao:
        colunas = {
            linha[1] for linha in conexao.execute("PRAGMA table_info(ativos)")
        }
    assert "vencimento" in colunas


def test_arquivo_corrompido_e_planilha_sem_colunas_geram_falhas(
    tmp_path: Path,
) -> None:
    """Registra falhas e continua processando arquivos válidos."""
    pasta = tmp_path / "xlsx"
    pasta.mkdir()
    (pasta / "corrompido.xlsx").write_bytes("não é um arquivo Excel".encode("utf-8"))
    _criar_planilha(pasta / "vazio.xlsx", [["Observação"], ["sem posição"]])
    _criar_planilha(
        pasta / "valido.xlsx",
        [["Ativo", "Quantidade", "Preço Médio"], ["ITUB4", 2, 25]],
    )

    relatorio = importar_planilhas_corretora(
        pasta=pasta,
        database=_database(tmp_path),
    )

    assert relatorio.arquivos_processados == 1
    assert len(relatorio.erros) == 2
    assert len(relatorio.erros) == len(
        [item for item in _database(tmp_path).listar_importacoes() if item["status"] == "erro"]
    )
